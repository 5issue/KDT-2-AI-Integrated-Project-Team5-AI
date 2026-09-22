"""추천 SQL 행을 문구 생성 입력으로 옮깁니다. **사실은 SQL 이 정하고, 여기서는 옮기고 검증만 합니다.**"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

MATCH_RATE_TOLERANCE = 0.0005


@dataclass(slots=True)
class RecipeFacts:
    """문구 하나를 만들 때 쓰는 사실 전부. ``my_recipe_candidates`` 행과 같은 모양입니다."""

    recipe: str
    have: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # 상비 재료. 레시피에 필요하지만 집에 있다고 보는 것. 사라고 하면 안 됩니다.
    pantry: list[str] = field(default_factory=list)
    cook_time_min: int | None = None
    required_count: int | None = None
    available_count: int | None = None
    missing_count: int | None = None
    match_rate: float | None = None

    @property
    def effective_missing(self) -> list[str]:
        """상비 재료를 뺀, 실제로 사거나 준비해야 하는 부족 재료."""
        return [name for name in self.missing if name not in self.pantry]

    @property
    def known_ingredients(self) -> set[str]:
        """이 상황에서 언급해도 되는 재료 전부."""
        return {*self.have, *self.missing, *self.pantry}


def _names(row: dict[str, Any], key: str) -> list[str]:
    """jsonb 목록 컬럼. asyncpg 기본 설정에서는 str 로 오므로 파싱합니다. 없으면 빈 목록."""
    value = row.get(key, [])
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value) if value.strip() else []
    if not isinstance(value, list):
        raise ValueError(f"{key}는 목록이어야 합니다.")
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and "name" in item:
            names.append(str(item["name"]))
        elif isinstance(item, str):
            names.append(item)
        else:
            raise ValueError(f"{key}의 각 항목에는 name이 필요합니다.")
    return names


def validate_facts(facts: RecipeFacts) -> None:
    """목록·count·보유율이 같은 사실을 가리키는지 확인합니다. 어긋나면 문구를 만들지 않습니다."""
    counts = (facts.required_count, facts.available_count, facts.missing_count)
    if all(value is None for value in counts):
        return
    if any(value is None for value in counts):
        raise ValueError("required_count, available_count, missing_count는 함께 필요합니다.")
    required, available, missing_count = (int(value) for value in counts if value is not None)
    if len(facts.have) + len(facts.pantry) != available:
        raise ValueError("보유·상비 재료 목록 수와 available_count가 일치하지 않습니다.")
    if len(facts.effective_missing) != missing_count:
        raise ValueError("부족 재료 목록 수와 missing_count가 일치하지 않습니다.")
    if required != available + missing_count:
        raise ValueError("available_count와 missing_count의 합이 required_count와 일치하지 않습니다.")
    if required == 0:
        raise ValueError("required_count는 0일 수 없습니다.")
    if facts.match_rate is not None and abs(facts.match_rate - available / required) > MATCH_RATE_TOLERANCE:
        raise ValueError("match_rate가 available_count / required_count와 일치하지 않습니다.")


def facts_from_row(row: dict[str, Any]) -> RecipeFacts:
    """``my_recipe_candidates`` 행 하나를 옮깁니다. 불변식 검사는 ``validate_facts`` 가 따로 합니다."""
    return RecipeFacts(
        recipe=str(row["name"]),
        have=_names(row, "held_ingredients"),
        missing=_names(row, "missing_ingredients"),
        pantry=_names(row, "pantry_ingredients"),
        cook_time_min=row.get("cook_time_min"),
        required_count=None if row.get("required_count") is None else int(row["required_count"]),
        available_count=None if row.get("available_count") is None else int(row["available_count"]),
        missing_count=None if row.get("missing_count") is None else int(row["missing_count"]),
        match_rate=None if row.get("match_rate") is None else float(row["match_rate"]),
    )
