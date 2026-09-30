"""7~11 구조 규칙. 문구가 카드 계약(두 문장, 길이, 문장 역할)을 지키는지 봅니다."""

from __future__ import annotations

import re

from .base import Check, CheckTarget
from .text import mentioned, split_sentences

# 문구 계약. prompt.py 의 CONTRACT_LINE 과 같이 바꿉니다.
MIN_CHARS, MAX_CHARS = 40, 120
SENTENCES = 2

_RATIO_OR_COUNT = re.compile(r"\d+\s*/\s*\d+|\d+\s*(?:개|가지)\s*중|\d+\s*%|\d+\s*퍼센트")
_SENTENCE_ENDINGS = re.compile(r"[.!?。？！]+")
_LIST_OR_MARKUP_START = re.compile(r"^\s*(?:[-*•]\s|\d+[.)]\s|```|\{\s*['\"])")


def repeated_numbers(target: CheckTarget) -> Check:
    """7. 화면 배지와 겹치는 숫자(비율, "6개 중")."""
    counted = _RATIO_OR_COUNT.search(target.reason)
    return Check("숫자_반복", counted is None, counted.group() if counted else "")


def length_limits(target: CheckTarget) -> list[Check]:
    """8. 길이. 빈 문구는 9 번이 따로 잡으므로 길이미달로 치지 않습니다."""
    length = len(target.reason)
    short = bool(target.reason.strip()) and length < MIN_CHARS
    return [
        Check("길이초과", length <= MAX_CHARS, f"{length}자" if length > MAX_CHARS else ""),
        Check("길이미달", not short, f"{length}자" if short else ""),
    ]


def empty_reason(target: CheckTarget) -> Check:
    """9. 빈 문구."""
    return Check("빈_문구", bool(target.reason.strip()))


def _endings(reason: str) -> list[str]:
    return _SENTENCE_ENDINGS.findall(reason.strip())


def is_malformed(reason: str) -> bool:
    """한 문단, 허용 문장 수, 목록·마크업 아님. 빈 문구는 형식 오류로 치지 않습니다."""
    return bool(reason.strip()) and (
        "\n" in reason
        or "\r" in reason
        or len(_endings(reason)) != SENTENCES
        or _LIST_OR_MARKUP_START.search(reason) is not None
    )


def malformed_output(target: CheckTarget) -> Check:
    """10. 한 문단, 허용 문장 수."""
    malformed = is_malformed(target.reason)
    return Check("출력_형식오류", not malformed, f"{len(_endings(target.reason))}문장" if malformed else "")


def sentence_roles(target: CheckTarget) -> Check | None:
    """11. 문장 역할. 첫 문장은 가진 재료, 둘째 문장은 무엇을 더 담으면 되는지.

    빈 문구나 형식 오류면 문장을 나눌 수 없어 검사하지 않습니다(None).
    """
    if not target.reason.strip() or is_malformed(target.reason):
        return None
    facts = target.facts
    missing = facts.effective_missing
    everything = facts.known_ingredients
    sentences = split_sentences(target.scannable)
    first = sentences[0] if sentences else ""
    rest = " ".join(sentences[1:])
    problems: list[str] = []
    if facts.have and not any(mentioned(name, first, others=everything) for name in facts.have):
        problems.append("첫 문장에 보유 재료가 없음")
    if any(mentioned(name, first, others=everything) for name in missing):
        problems.append("첫 문장에 부족 재료가 있음")
    absent = [name for name in missing if not mentioned(name, rest, others=everything)]
    if 1 <= len(missing) <= 3 and absent:
        problems.append("둘째 문장에 빠진 부족 재료: " + ", ".join(absent))
    elif len(missing) >= 4 and absent and "가지" not in rest and "재료" not in rest:
        # 4개 이상이면 "부족한 재료 몇 가지" 로 뭉뚱그려도 되고, 전부 나열해도 됩니다. 둘 다 아니면 실패.
        problems.append("둘째 문장에 부족 재료 안내가 없음")
    return Check("문장_역할", not problems, "; ".join(problems))
