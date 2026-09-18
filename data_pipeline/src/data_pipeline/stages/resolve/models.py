"""3단계가 주고받는 자료형 셋.

`NameRequest` 가 들어가고 `MatchResult` 가 나오며, `ResolveReport` 가 그것을 모아
사람이 읽을 수 있게 합니다. 나머지 모듈이 전부 이 셋을 공유해서 따로 두었습니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NameRequest:
    """매칭을 요청할 재료명 하나."""

    normalized_name: str
    display_name: str
    sample_raw_text: str
    occurrence: int


@dataclass(slots=True)
class MatchResult:
    """확정된 매칭 하나."""

    normalized_name: str
    ingredient_id: int
    matched_name: str
    method: str
    confidence: float


@dataclass(slots=True)
class ResolveReport:
    """3단계 결과 요약."""

    total_names: int = 0
    exact: list[MatchResult] = field(default_factory=list)
    llm: list[MatchResult] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)

    @property
    def matched(self) -> list[MatchResult]:
        """정확 일치 + LLM 매칭."""
        return [*self.exact, *self.llm]

    def render(self) -> str:
        """사람이 읽을 요약."""
        rate = len(self.matched) / self.total_names if self.total_names else 0.0
        lines = [
            f"매칭 대상 재료명 : {self.total_names}종",
            f"정확 일치        : {len(self.exact)}종",
            f"LLM 매칭         : {len(self.llm)}종",
            f"미매칭           : {len(self.unmatched)}종",
            f"매칭률           : {rate:.1%}",
        ]
        if self.unmatched:
            lines.append("\n마스터에 없어 건너뛴 재료 (검토 필요):")
            for item in self.unmatched[:30]:
                lines.append(
                    f"  {item['normalized_name']:<16} {item.get('occurrence', 0):>4}회  "
                    f"conf={item.get('confidence', 0):.2f}  {str(item.get('reason', ''))[:50]}"
                )
            if len(self.unmatched) > 30:
                lines.append(f"  ... 외 {len(self.unmatched) - 30}종")
        return "\n".join(lines)
