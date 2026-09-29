"""문구가 사실과 어긋나지 않는지 보는 **자동 검사**. LLM 을 쓰지 않습니다.

검사는 세 갈래이고, 하나라도 어기면 문구를 내보내지 않습니다(서빙은 규칙 문구로 바꿉니다).
규칙 하나하나는 ``util/`` 에 있고, 여기서는 순서대로 부르기만 합니다.

- 사실성 1~6 (``util/factuality.py``): 재료·시간·상비재료·건강에 대해 상황에 없는 말을 하는가
- 구조 7~11 (``util/structure.py``): 두 문장, 길이, 첫 문장은 가진 재료·둘째 문장은 더 담을 것
- 간접 주입 12~14 (``util/injection.py``): 레시피명·재료명에 숨은 지시를 따른 흔적.
  2026-09-29 안전성 검증(`notebooks/ai_safety_eval.ipynb`)에서 사용자에게 5건 노출된 뒤 더했습니다.

``rag_lab.recommendation.checks`` 에서 서빙에 필요한 항목만 옮겼습니다.
"""

from __future__ import annotations

from collections.abc import Iterable

from .facts import RecipeFacts
from .util import factuality, injection, structure
from .util.base import Check, CheckTarget
from .util.structure import MAX_CHARS, MIN_CHARS, SENTENCES

__all__ = ["MAX_CHARS", "MIN_CHARS", "SENTENCES", "Check", "check_reason", "failed_names"]


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
    target = CheckTarget.of(facts, reason, foreign_ingredients=foreign_ingredients, vocabulary=vocabulary)
    checks = [
        factuality.other_card_ingredients(target),  # 1 다른카드_재료혼입
        factuality.invented_ingredients(target),  # 2 환각_재료
        factuality.flipped_state(target),  # 3 보유_뒤집힘
        factuality.pantry_purchase(target),  # 4 상비재료_구매유도
        factuality.invented_cook_time(target),  # 5 조리시간_지어냄
        factuality.unsupported_health_claim(target),  # 6 근거_없는_영양건강주장
        structure.repeated_numbers(target),  # 7 숫자_반복
        *structure.length_limits(target),  # 8 길이초과, 길이미달
        structure.empty_reason(target),  # 9 빈_문구
        structure.malformed_output(target),  # 10 출력_형식오류
    ]
    # 11 문장_역할. 빈 문구나 형식 오류면 문장을 나눌 수 없어 결과가 없습니다.
    if (roles := structure.sentence_roles(target)) is not None:
        checks.append(roles)
    checks += [
        injection.foreign_ascii_tokens(target),  # 12 입력에_없는_영문숫자
        injection.planted_link(target),  # 13 링크_삽입
        injection.food_safety_smear(target),  # 14 음식안전_비방
    ]
    return checks


def failed_names(checks: Iterable[Check]) -> list[str]:
    return [check.name for check in checks if not check.passed]
