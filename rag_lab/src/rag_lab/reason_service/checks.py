"""문구가 사실과 어긋나지 않는지 보는 **자동 검사**. LLM 을 쓰지 않습니다.

검사는 두 갈래입니다. 사실성(재료·시간·상비재료)과 구조(문장 수·역할·길이). 둘 다 어기면 문구를 내보내지 않습니다.

``rag_lab.recommendation.checks`` 에서 서빙에 필요한 항목만 옮겼습니다. 한국어 재료명의 동음이의어
오탐 목록(`가지`, `배`, `마`)은 실험에서 실제로 부딪힌 것들이며, 지우기 전에 테스트를 먼저 보세요.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .facts import RecipeFacts

# 목적격 조사 `을`/`를`도 넣습니다. `된장을 사서` 는 상비재료 구매 유도입니다.
_PARTICLES = r"(?:은|는|이|가|도|만|과|와|랑|이랑|을|를)?"
_HAS_WORDS = r"(?:있(?!으면|다면|으시면)|남았|갖(?!춘|추)|보유|충분)"
_LACKS_WORDS = r"(?:없|부족|빠졌|모자라|떨어)"
_BUY_WORDS = r"(?:사|사서|사면|구매|주문|장바구니|담으|준비하)"

# 재료명이면서 흔한 다른 말이기도 한 것. 해당 문맥에서는 재료로 보지 않습니다.
# `가지`: 수량 단위(`네 가지`)와 동사(`가지고`). `배`: `배가시키다`, `양념이 배는`, `배어든`.
# `마`: `마저`, `마지막`, `마무리`. 실험 중 확인된 오탐만 넣습니다.
_AMBIGUOUS_WORDS: dict[str, str] = {
    "가지": r"(?:(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇|여러|\d+)\s*가지|가지(?=[고면며]))",
    "배": r"(?:배가(?=[시하되])|(?<=[가-힣]이 )배는|배어|배는|배게)",
    "마": r"(?:마저|마지막|마무리|마련|마음|마냥)",
}

_TIME_MENTION = re.compile(r"\d+\s*(?:분|시간)")
_UNSUPPORTED_NUTRITION_HEALTH = re.compile(
    r"칼로리|열량|단백질|탄수화물|나트륨|콜레스테롤|혈당|혈압|면역(?:력)?|"
    r"다이어트|체중\s*감량|항산화|해독|영양(?:소)?|건강"
    r"|\d+(?:[.,]\d+)?\s*(?:kcal|mg|g|그램|밀리그램|%|퍼센트)",
    re.IGNORECASE,
)
_SENTENCE_ENDINGS = re.compile(r"[.!?。？！]+")
_LIST_OR_MARKUP_START = re.compile(r"^\s*(?:[-*•]\s|\d+[.)]\s|```|\{\s*['\"])")
_RATIO_OR_COUNT = re.compile(r"\d+\s*/\s*\d+|\d+\s*(?:개|가지)\s*중|\d+\s*%|\d+\s*퍼센트")

# 문구 계약. prompt.py 의 CONTRACT_LINE 과 같이 바꿉니다.
MIN_CHARS, MAX_CHARS = 40, 120
SENTENCES = 2


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


def _word_pattern(name: str) -> str:
    # 앞에 한글이 붙어 있으면 다른 단어의 일부입니다(`양파` 안의 `파`).
    return r"(?<![가-힣])" + re.escape(name)


def _mentions(reason: str, name: str, verb_group: str) -> bool:
    return re.search(_word_pattern(name) + _PARTICLES + r"\s*" + verb_group, reason) is not None


def _appears_as_ingredient(word: str, reason: str) -> bool:
    if re.search(_word_pattern(word), reason) is None:
        return False
    if pattern := _AMBIGUOUS_WORDS.get(word):
        return re.search(_word_pattern(word), re.sub(pattern, " ", reason)) is not None
    return True


def _is_part_of_known(word: str, known: set[str]) -> bool:
    return any(word != name and word in name for name in known)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+")


def _split_sentences(reason: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(reason.strip()) if part]


def _aliases(name: str) -> list[str]:
    """`파프리카(착색단고추)` 는 `파프리카` 로도, `착색단고추` 로도 불립니다."""
    aliases = [name]
    if "(" in name and name.endswith(")"):
        base, inner = name[:-1].split("(", 1)
        aliases += [part for part in (base.strip(), inner.strip()) if part]
    return aliases


def _mentioned(name: str, text: str, *, others: Iterable[str] = ()) -> bool:
    """재료가 언급됐는가.

    `새송이버섯` 을 `버섯` 으로 줄여 부른 것도 인정합니다. 단, 그 줄임말이 이 상황의 **다른** 재료
    안에도 들어 있으면 인정하지 않습니다. `방울토마토` 의 `마토` 로 `토마토` 언급을 잡으면 안 됩니다.
    """
    for alias in _aliases(name):
        if re.search(_word_pattern(alias), text) is not None:
            return True
        # ponytail: 뒤 두 글자 휴리스틱. `돼지고기` 를 `고기` 로 부른 것도 통과함. 오탐이 보이면 재료별 별칭 표로.
        suffix = alias[-2:]
        if len(alias) > 2 and suffix in text and not any(suffix in other for other in others if other != name):
            return True
    return False


def _title_context_only(word: str, facts: RecipeFacts, text: str) -> bool:
    """레시피 제목에 든 재료를 요리 맥락으로만 말한 경우. 보유·부재·구매 주장을 하면 예외가 아닙니다."""
    if word not in facts.recipe:
        return False
    pattern = r"(?<![가-힣])" + re.escape(word) + _PARTICLES + r"[^.!?。？！\n]{0,10}"
    return not any(re.search(pattern + group, text) for group in (_HAS_WORDS, _LACKS_WORDS, _BUY_WORDS))


def check_reason(
    facts: RecipeFacts,
    reason: str,
    *,
    foreign_ingredients: Iterable[str] = (),
    vocabulary: Iterable[str] = (),
) -> list[Check]:
    """검사 결과 목록. 하나라도 ``passed=False`` 면 그 문구는 내보내지 않습니다.

    ``foreign_ingredients`` 는 같은 화면의 다른 카드 재료입니다. 카드 3장을 함께 만들 때 옆 카드의
    재료가 넘어왔는지 잡습니다.

    ``vocabulary`` 는 재료 이름 전체 사전입니다(예: ``ingredient`` 표). 이 상황에도, 옆 카드에도 없는
    재료를 지어냈는지 잡습니다. 실험에서는 사례 40건의 재료를 사전으로 썼는데, 서빙은 요청 하나에
    카드 3장뿐이라 사전이 없으면 이 검사가 사실상 빠집니다. 그래서 호출하는 쪽이 사전을 넘깁니다.
    한 글자 재료(`파`, `무`, `배`, `김`)는 오탐이 많아 사전에서는 뺍니다.
    """
    checks: list[Check] = []
    known = facts.known_ingredients
    missing = facts.effective_missing
    # 레시피 이름 안의 재료는 제목 인용이므로 그 구간만 지우고 검사합니다.
    scannable = reason.replace(facts.recipe, " ") if facts.recipe else reason

    # 1. 같은 화면 다른 카드의 재료가 섞였는가
    mixed = sorted(
        word
        for word in set(foreign_ingredients) - known
        if not _is_part_of_known(word, known)
        and _appears_as_ingredient(word, scannable)
        and not _title_context_only(word, facts, scannable)
    )
    checks.append(Check("다른카드_재료혼입", not mixed, ", ".join(mixed)))

    # 2. 사전에는 있지만 이 상황에는 없는 재료를 말했는가
    invented = sorted(
        word
        for word in set(vocabulary) - known - set(foreign_ingredients)
        if len(word) >= 2
        and word in scannable  # 정규식 전에 싸게 거름. 사전이 re 캐시(512)보다 커서 매번 재컴파일되는 것을 막음
        and not _is_part_of_known(word, known)
        and _appears_as_ingredient(word, scannable)
        and not _title_context_only(word, facts, scannable)
    )
    checks.append(Check("환각_재료", not invented, ", ".join(invented)))

    # 3. 보유/부족 뒤집힘. 상비 재료는 집에 있다고 보므로 뺍니다.
    flipped = [name for name in missing if _mentions(scannable, name, _HAS_WORDS)]
    flipped += [name for name in facts.have if _mentions(scannable, name, _LACKS_WORDS)]
    checks.append(Check("보유_뒤집힘", not flipped, ", ".join(flipped)))

    # 4. 상비 재료를 사라고 했는가
    pushed = [name for name in facts.pantry if _mentions(scannable, name, _BUY_WORDS)]
    checks.append(Check("상비재료_구매유도", not pushed, ", ".join(pushed)))

    # 5. 없는 조리시간을 지어냈는가
    time_hit = _TIME_MENTION.search(reason)
    invented_time = facts.cook_time_min is None and time_hit is not None
    checks.append(Check("조리시간_지어냄", not invented_time, time_hit.group() if invented_time and time_hit else ""))

    # 6. 근거 없는 영양·건강 주장
    health = _UNSUPPORTED_NUTRITION_HEALTH.search(scannable)
    checks.append(Check("근거_없는_영양건강주장", health is None, health.group() if health else ""))

    # 7. 화면 배지와 겹치는 숫자
    counted = _RATIO_OR_COUNT.search(reason)
    checks.append(Check("숫자_반복", counted is None, counted.group() if counted else ""))

    # 8. 길이
    length = len(reason)
    checks.append(Check("길이초과", length <= MAX_CHARS, f"{length}자" if length > MAX_CHARS else ""))
    short = bool(reason.strip()) and length < MIN_CHARS
    checks.append(Check("길이미달", not short, f"{length}자" if short else ""))

    # 9. 빈 문구
    checks.append(Check("빈_문구", bool(reason.strip())))

    # 10. 한 문단, 허용 문장 수
    endings = _SENTENCE_ENDINGS.findall(reason.strip())
    malformed = bool(reason.strip()) and (
        "\n" in reason
        or "\r" in reason
        or len(endings) != SENTENCES
        or _LIST_OR_MARKUP_START.search(reason) is not None
    )
    checks.append(Check("출력_형식오류", not malformed, f"{len(endings)}문장" if malformed else ""))

    # 11. 문장 역할. 첫 문장은 가진 재료, 둘째 문장은 무엇을 더 담으면 되는지.
    if reason.strip() and not malformed:
        sentences = _split_sentences(scannable)
        first = sentences[0] if sentences else ""
        rest = " ".join(sentences[1:])
        problems: list[str] = []
        everything = known
        if facts.have and not any(_mentioned(name, first, others=everything) for name in facts.have):
            problems.append("첫 문장에 보유 재료가 없음")
        if any(_mentioned(name, first, others=everything) for name in missing):
            problems.append("첫 문장에 부족 재료가 있음")
        absent = [name for name in missing if not _mentioned(name, rest, others=everything)]
        if 1 <= len(missing) <= 3 and absent:
            problems.append("둘째 문장에 빠진 부족 재료: " + ", ".join(absent))
        elif len(missing) >= 4 and absent and "가지" not in rest and "재료" not in rest:
            # 4개 이상이면 "부족한 재료 몇 가지" 로 뭉뚱그려도 되고, 전부 나열해도 됩니다. 둘 다 아니면 실패.
            problems.append("둘째 문장에 부족 재료 안내가 없음")
        checks.append(Check("문장_역할", not problems, "; ".join(problems)))
    return checks


def failed_names(checks: Iterable[Check]) -> list[str]:
    return [check.name for check in checks if not check.passed]
