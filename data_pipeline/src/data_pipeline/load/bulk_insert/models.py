"""staging 컬럼 순서와 결과 자료형.

`STAGING_*` 는 `sql/001_staging_tables.sql` 의 컬럼 순서와 **같아야 합니다.**
`copy_records_to_table` 은 이름이 아니라 순서로 넣기 때문에, 어긋나도 에러가 나지 않고
값이 옆 칸에 들어갑니다. 컬럼을 추가할 때는 SQL 과 여기를 함께 고치세요.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STAGING_RECIPE_COLUMNS = (
    "source_type",
    "source_id",
    "name",
    "description",
    "cuisine_type",
    "difficulty",
    "prep_time_min",
    "cook_time_min",
    "servings",
    "cooking_method",
    "nutrition",
    "tags",
    "image_url",
)
STAGING_RECIPE_STEP_COLUMNS = ("source_type", "source_id", "step_no", "instruction", "image_url")
STAGING_RECIPE_INGREDIENT_COLUMNS = (
    "source_type",
    "source_id",
    "line_no",
    "raw_text",
    "name",
    "normalized_name",
    "quantity",
    "unit",
    "is_required",
    "purpose",
)
STAGING_STORAGE_COLUMNS = (
    "source_item_id",
    "source_food_name",
    "source_food_subtitle",
    "normalized_name",
    "source_slot",
    "storage_location",
    "storage_context",
    "duration_min",
    "duration_max",
    "duration_unit",
    "duration_text",
    "storage_tips",
)
STAGING_MATCH_COLUMNS = ("normalized_name", "ingredient_id", "matched_name", "method", "confidence")


@dataclass(slots=True)
class StagingRows:
    """COPY 로 밀어넣을 행 묶음."""

    recipes: list[tuple[Any, ...]] = field(default_factory=list)
    recipe_ingredients: list[tuple[Any, ...]] = field(default_factory=list)
    recipe_steps: list[tuple[Any, ...]] = field(default_factory=list)
    storage: list[tuple[Any, ...]] = field(default_factory=list)
    matches: list[tuple[Any, ...]] = field(default_factory=list)
    skipped_ingredients: int = 0
    skipped_storage: int = 0
    skipped_steps: int = 0
    skipped_recipes: int = 0

    def is_empty(self) -> bool:
        """적재할 것이 하나도 없는지."""
        return not (self.recipes or self.storage)


@dataclass(slots=True)
class LoadReport:
    """적재 결과 요약."""

    staged: dict[str, int] = field(default_factory=dict)
    applied_sql: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    skipped_ingredients: int = 0
    skipped_storage: int = 0
    skipped_steps: int = 0
    skipped_recipes: int = 0

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [f"{name:<28}: {count}행" for name, count in sorted(self.staged.items())]
        if self.skipped_ingredients:
            lines.append(f"{'매칭 실패로 건너뛴 재료줄':<28}: {self.skipped_ingredients}행")
        if self.skipped_storage:
            lines.append(f"{'매칭 실패로 건너뛴 보관기준':<28}: {self.skipped_storage}행")
        if self.skipped_steps:
            lines.append(f"{'내용이 비어 건너뛴 조리단계':<28}: {self.skipped_steps}행")
        if self.skipped_recipes:
            lines.append(f"{'재료 매칭률 미달로 뺀 레시피':<28}: {self.skipped_recipes}건")
        lines.append(f"{'적용한 SQL':<28}: {', '.join(self.applied_sql) or '없음'}")
        lines.extend(f"{table:<28}: {count}행 (적재 후)" for table, count in sorted(self.row_counts.items()))
        return "\n".join(lines)
