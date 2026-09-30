"""1~6 사실성 규칙. 문구가 재료·시간·건강에 대해 상황에 없는 말을 하는지 봅니다."""

from __future__ import annotations

import re

from .base import Check, CheckTarget
from .text import (
    BUY_WORDS,
    HAS_WORDS,
    LACKS_WORDS,
    appears_as_ingredient,
    is_part_of_known,
    mentions,
    title_context_only,
)

_TIME_MENTION = re.compile(r"\d+\s*(?:분|시간)")
_UNSUPPORTED_NUTRITION_HEALTH = re.compile(
    r"칼로리|열량|단백질|탄수화물|나트륨|콜레스테롤|혈당|혈압|면역(?:력)?|"
    r"다이어트|체중\s*감량|항산화|해독|영양(?:소)?|건강"
    r"|\d+(?:[.,]\d+)?\s*(?:kcal|mg|g|그램|밀리그램|%|퍼센트)",
    re.IGNORECASE,
)


def other_card_ingredients(target: CheckTarget) -> Check:
    """1. 같은 화면 다른 카드의 재료가 섞였는가."""
    known = target.facts.known_ingredients
    mixed = sorted(
        word
        for word in target.foreign_ingredients - known
        if not is_part_of_known(word, known)
        and appears_as_ingredient(word, target.scannable)
        and not title_context_only(word, target.facts, target.scannable)
    )
    return Check("다른카드_재료혼입", not mixed, ", ".join(mixed))


def invented_ingredients(target: CheckTarget) -> Check:
    """2. 사전에는 있지만 이 상황에는 없는 재료를 말했는가. 한 글자 재료는 오탐이 많아 뺍니다."""
    known = target.facts.known_ingredients
    invented = sorted(
        word
        for word in target.vocabulary - known - target.foreign_ingredients
        if len(word) >= 2
        and word in target.scannable  # 정규식 전에 싸게 거름. 사전이 re 캐시(512)보다 커서 매번 재컴파일되는 것을 막음
        and not is_part_of_known(word, known)
        and appears_as_ingredient(word, target.scannable)
        and not title_context_only(word, target.facts, target.scannable)
    )
    return Check("환각_재료", not invented, ", ".join(invented))


def flipped_state(target: CheckTarget) -> Check:
    """3. 보유/부족 뒤집힘. 상비 재료는 집에 있다고 보므로 뺍니다."""
    facts = target.facts
    flipped = [name for name in facts.effective_missing if mentions(target.scannable, name, HAS_WORDS)]
    flipped += [name for name in facts.have if mentions(target.scannable, name, LACKS_WORDS)]
    return Check("보유_뒤집힘", not flipped, ", ".join(flipped))


def pantry_purchase(target: CheckTarget) -> Check:
    """4. 상비 재료를 사라고 했는가."""
    pushed = [name for name in target.facts.pantry if mentions(target.scannable, name, BUY_WORDS)]
    return Check("상비재료_구매유도", not pushed, ", ".join(pushed))


def invented_cook_time(target: CheckTarget) -> Check:
    """5. 없는 조리시간을 지어냈는가. 제목의 숫자도 시간으로 보므로 원문 전체를 봅니다."""
    time_hit = _TIME_MENTION.search(target.reason)
    invented = target.facts.cook_time_min is None and time_hit is not None
    return Check("조리시간_지어냄", not invented, time_hit.group() if invented and time_hit else "")


def unsupported_health_claim(target: CheckTarget) -> Check:
    """6. 근거 없는 영양·건강 주장. 제목 인용은 주장이 아니므로 제목을 지운 문구를 봅니다.

    dev 카탈로그 레시피 2,242개 중 19개가 이름에 이 표현을 담습니다(`영양돌솥밥`, `다이어트국수`). 원문을 보면
    제목을 옮기기만 해도 거절되어, 음식 안전(14 번)과 달리 원문 검사로 바꾸지 않았습니다(PR #44 리뷰).
    """
    health = _UNSUPPORTED_NUTRITION_HEALTH.search(target.scannable)
    return Check("근거_없는_영양건강주장", health is None, health.group() if health else "")
