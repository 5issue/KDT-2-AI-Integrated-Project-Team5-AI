"""규칙 기반 문구. LLM 이 실패했을 때, 또는 LLM 을 끄면 이 문구가 나갑니다.

실험의 ``template_deterministic_v2`` 를 그대로 옮겼습니다. 실험에서 기존 서빙 문구보다 세 판정 모델
모두에서 높은 평가를 받았습니다.
"""

from __future__ import annotations

from .facts import RecipeFacts

MAX_TEMPLATE_CHARS = 60


def object_particle(text: str) -> str:
    """마지막 한글 음절의 받침으로 목적격 조사를 고릅니다. 받침이 있으면 `을`, 없으면 `를`."""
    for character in reversed(text):
        code = ord(character) - ord("가")
        if 0 <= code <= 11171:
            return "을" if code % 28 else "를"
    return "을"


def template_reason(facts: RecipeFacts) -> str:
    """부족 재료 수에 따라 네 갈래 중 하나를 냅니다. 60자를 넘으면 재료명 대신 개수를 씁니다."""
    missing = facts.effective_missing
    count = len(missing)
    recipe = facts.recipe
    particle = object_particle(recipe)

    if count == 0:
        return f"{recipe}{particle} 바로 만들어 보세요."

    if count == 1:
        if facts.have:
            first = facts.have[0]
            reason = (
                f"{first}{object_particle(first)} 활용하고 {missing[0]}만 더하면 {recipe}{particle} 만들 수 있어요."
            )
            if len(reason) <= MAX_TEMPLATE_CHARS:
                return reason
        return f"{missing[0]}만 더하면 {recipe}{particle} 만들 수 있어요."

    if count <= 3:
        names = "·".join(missing[:2])
        reason = f"{names} 등 {count}가지를 더 준비하면 {recipe}{particle} 만들 수 있어요."
        if len(reason) <= MAX_TEMPLATE_CHARS:
            return reason
        return f"재료 {count}가지를 더 준비하면 {recipe}{particle} 만들 수 있어요."

    return f"{recipe}에 필요한 재료 {count}가지를 장보기 목록에서 확인해 보세요."
