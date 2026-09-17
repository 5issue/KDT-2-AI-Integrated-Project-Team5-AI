"""문구가 상황과 어긋나지 않는지 보는 **자동 검사**. LLM 을 쓰지 않습니다.

문구가 **틀렸는지**(없는 재료를 말함, 상비재료를 사라고 함, 없는 조리시간을 지어냄)는
문자열 검사로 잡힙니다. LLM 을 부를 일이 아니고, 부르면 채점이 흔들립니다.
문구가 **좋은 카피인지**는 규칙으로 못 잡습니다 - 그쪽은 `judge` 모듈입니다.

## 왜 이 파일이 긴가

한국어 재료명은 단순 문자열 검색으로 오탐이 많습니다 - `파`와 `양파`,
`배`와 `배가시키다`, `가지`와 `네 가지`. 여기 있는 예외는 장황함이 아니라
**실험에서 실제로 부딪혀 테스트로 고정한 것들**입니다. 지우기 전에
`rag_lab/tests/` 의 해당 회귀 테스트를 먼저 보세요.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rag_lab.recommendation.core import MAX_REASON_CHARS, RecommendationCase

# 문구가 재료를 "있다" / "없다" 중 어느 쪽으로 말했는지 보는 패턴.
# 조사는 붙을 수도 안 붙을 수도 있어 선택으로 둡니다 (`두부는 있으니` / `두부 있으니`).
_PARTICLES = r"(?:은|는|이|가|도|만|과|와|랑|이랑)?"
# `있으면` / `있다면` 은 가정입니다 - "닭 육수만 있으면 즐기실 수 있습니다" 는 보유
# 주장이 아니라 그 반대입니다. 첫 실험에서 이걸 뒤집힘으로 잡았습니다.
_HAS_WORDS = r"(?:있(?!으면|다면|으시면)|남았|갖(?!춘|추)|보유|충분)"
_LACKS_WORDS = r"(?:없|부족|빠졌|모자라|떨어)"
_BUY_WORDS = r"(?:사|사서|사면|구매|주문|장바구니|담으|준비하)"

# 재료명이면서 한국어의 흔한 다른 말이기도 한 것들. 문자열 검사로는 가릴 수 없어
# 문맥 패턴을 따로 둡니다. **부딪힌 것만 넣습니다.** 미리 채우면 진짜 환각을 놓칩니다.
#
# `가지`: 채소이면서 수량 단위(`재료 네 가지`)이자 동사(`가지고 계셔서`) 입니다.
# 부족 재료가 3개 이상일 때 개수로 줄여 쓰라고 시켰더니 바로 걸렸습니다.
_AMBIGUOUS_WORDS: dict[str, str] = {
    "가지": r"(?:(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇|여러|\d+)\s*가지|가지(?=[고면며]))",
    # `배`(과일) 와 `배가시키다`(늘리다). "호두의 고소함이 바나나의 달콤함을 배가시켜" 에서 걸렸습니다.
    "배": r"배가(?=[시하되])",
}

# 조리시간을 지어냈는지 보는 패턴. `20분`, `1시간`, `30 분` 을 잡습니다.
_TIME_MENTION = re.compile(r"\d+\s*(?:분|시간)")


@dataclass(slots=True)
class Check:
    """자동 검사 하나의 결과."""

    name: str
    passed: bool
    detail: str = ""


def _mentions(reason: str, name: str, verb_group: str) -> bool:
    """문구가 `name` 을 `verb_group` 쪽으로 말했는지.

    **앞에 한글이 붙어 있으면 다른 단어입니다.** `파` 를 찾을 때 `양파가 있어요` 가
    걸리면 안 됩니다. 마스터에 `파`·`무`·`배`·`김` 같은 한 글자 재료가 41종 있어서
    이 경계가 없으면 오탐이 쏟아집니다.
    """
    pattern = r"(?<![가-힣])" + re.escape(name) + _PARTICLES + r"\s*" + verb_group
    return re.search(pattern, reason) is not None


def _is_part_of_known(word: str, known: set[str]) -> bool:
    """`word` 가 이 상황의 재료 이름 안에 든 조각인가.

    `방울토마토` 를 가진 상황에서 다른 케이스의 `토마토` 가 침입자로 잡히는 것을 막습니다.
    """
    return any(word != name and word in name for name in known)


def _mask_recipe_name(reason: str, recipe: str) -> str:
    """문구에서 **레시피 이름이 나온 자리만** 지웁니다.

    레시피 이름은 상황이 준 값이라 문구에 그대로 나오는 것이 정상입니다. 그런데 그
    이름 안에 재료명이 들어 있으면(`물파래콩전` 의 `물`, `치즈토마토 가지구이` 의 `치즈`)
    환각으로 잡힙니다. 첫 베이스라인의 환각 3건이 전부 이 오탐이었습니다.

    **이름이 나온 구간만 지우고 나머지는 그대로 검사합니다.** 처음에는 "이름에 든
    글자면 통과" 로 두었는데, 그러면 `치즈토마토 가지구이` 상황에서 보유하지도 않은
    `치즈` 를 "치즈가 있으니 바로 만드세요" 라고 말해도 통과했습니다. 오탐을 막으려다
    정탐까지 막은 것입니다.
    """
    return reason.replace(recipe, " ") if recipe else reason


def _appears_as_ingredient(word: str, reason: str) -> bool:
    """`word` 가 문구에서 **재료로** 쓰였는가.

    앞에 한글이 붙어 있으면 다른 단어의 일부입니다(`양파` 안의 `파`).
    동음이의어는 `_AMBIGUOUS_WORDS` 의 문맥 패턴에 걸리면 재료가 아닌 것으로 봅니다.
    """
    if re.search(r"(?<![가-힣])" + re.escape(word), reason) is None:
        return False
    if pattern := _AMBIGUOUS_WORDS.get(word):
        # 다른 뜻으로 쓰인 자리를 지우고 나서도 남아 있어야 재료입니다.
        return re.search(r"(?<![가-힣])" + re.escape(word), re.sub(pattern, " ", reason)) is not None
    return True


def check_reason(
    case: RecommendationCase,
    reason: str,
    *,
    vocabulary: Iterable[str] = (),
    max_chars: int = MAX_REASON_CHARS,
) -> list[Check]:
    """문구가 상황과 어긋나지 않는지 봅니다. **LLM 을 쓰지 않습니다.**

    `vocabulary` 는 "재료로 인정할 이름" 의 목록입니다. 케이스 파일 전체의 재료를
    모아 넘기면, 다른 케이스에나 나올 재료가 이 문구에 섞였을 때 잡힙니다.
    비워 두면 환각 검사만 건너뛰고 나머지는 그대로 돕니다.
    """
    checks: list[Check] = []
    known = case.known_ingredients
    # 레시피 이름이 나온 자리를 지우고 검사합니다. 이름 안의 글자는 재료 언급이 아닙니다.
    # 이름 **밖**의 언급은 그대로 검사 대상입니다.
    scannable = _mask_recipe_name(reason, case.recipe)

    # 1. 환각 - 이 상황에 없는 재료를 말했는가
    intruders = sorted(
        word
        for word in vocabulary
        if word not in known and not _is_part_of_known(word, known) and _appears_as_ingredient(word, scannable)
    )
    checks.append(Check("환각_재료", not intruders, f"상황에 없는 재료: {', '.join(intruders)}" if intruders else ""))

    # 2. 보유/부족 뒤집힘 - 없는 것을 있다고, 있는 것을 없다고
    #
    # **상비 재료는 뺍니다.** `missing` 에 들어 있어도 집에 있다고 보는 것들이라
    # (프롬프트도 그렇게 지시합니다), `소금이 있으니` 는 맞는 말입니다.
    # 빼지 않으면 올바른 문구가 뒤집힘으로 잡힙니다.
    lacking = [name for name in case.missing if name not in case.pantry]
    flipped = [name for name in lacking if _mentions(scannable, name, _HAS_WORDS)]
    flipped += [name for name in case.have if _mentions(scannable, name, _LACKS_WORDS)]
    checks.append(Check("보유_뒤집힘", not flipped, f"뒤집힌 재료: {', '.join(flipped)}" if flipped else ""))

    # 3. 상비재료를 사라고 했는가
    #
    # 상비재료는 `missing` 에 들어 있어도 장바구니 대상이 아닙니다. 소금을 사라고 하면
    # 데모에서 바로 눈에 띕니다.
    pushed = [name for name in case.pantry if _mentions(scannable, name, _BUY_WORDS)]
    checks.append(Check("상비재료_구매유도", not pushed, f"사라고 한 상비재료: {', '.join(pushed)}" if pushed else ""))

    # 4. 없는 조리시간을 지어냈는가
    #
    # COOKRCP01 레시피는 `cook_time_min` 이 전부 NULL 입니다. 시간을 말하면 전부 환각입니다.
    invented = case.cook_time_min is None and bool(_TIME_MENTION.search(reason))
    checks.append(Check("조리시간_지어냄", not invented, _TIME_MENTION.search(reason).group() if invented else ""))  # type: ignore[union-attr]

    # 5. 길이
    over = len(reason) > max_chars
    checks.append(Check("길이초과", not over, f"{len(reason)}자 / 상한 {max_chars}자" if over else ""))

    # 6. 비어 있는가. 모델이 빈 문자열을 내면 위 검사가 전부 통과해 버립니다.
    checks.append(Check("빈_문구", bool(reason.strip()), "문구가 비었습니다" if not reason.strip() else ""))

    return checks


def collect_vocabulary(cases: Sequence[RecommendationCase]) -> set[str]:
    """케이스 전체의 재료 이름. 환각 검사의 사전이 됩니다.

    케이스 파일 안에서만 모읍니다. 재료 마스터 1,028종을 전부 쓰면 `무`·`배`·`김` 같은
    한 글자 재료가 아무 문장에나 걸려 오탐이 쏟아집니다.
    """
    return {name for case in cases for name in case.known_ingredients}


# ---------------------------------------------------------------------------
# 실험 실행
#
# `experiment.py` 의 질문 세트 실험과 나란히 두지 않고 여기 둡니다. 그쪽은 팀원이
# 라우팅/검색 실험으로 계속 고치는 파일이라, 같은 파일을 양쪽에서 건드리면 충돌합니다.
# 결과 JSONL 형식과 파라미터 스냅샷 규칙은 그대로 따릅니다(`snapshot_params` 재사용).
