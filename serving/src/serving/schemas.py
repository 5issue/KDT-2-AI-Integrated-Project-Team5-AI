"""API 응답 스키마 (Pydantic v2)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """liveness 응답."""

    status: str = "ok"
    version: str
    environment: str


class DbHealthResponse(BaseModel):
    """readiness 응답. 호스트나 자격증명은 담지 않습니다."""

    ok: bool
    latency_ms: float
    database: str | None = None
    server_version: str | None = None
    pool_size: int | None = None
    pool_idle: int | None = None
    detail: str | None = None
    notes: list[str] = Field(default_factory=list)


class MissingIngredientRef(BaseModel):
    """부족 재료 참조 (명세 21장 missing_ingredients 항목)."""

    ingredient_id: int
    name: str


class RecipeMatch(BaseModel):
    """재료 매칭 요약 (명세 21장 match 객체)."""

    required_ingredients: int
    available_ingredients: int
    missing_ingredients: int
    match_rate: float


class MyRecipeItem(BaseModel):
    """My냉장고 기반 레시피 추천 한 건 (명세 21장)."""

    recipe_id: int
    name: str
    difficulty: str | None = None
    cook_time_min: int | None = None
    servings: int | None = None
    recommendation_reason: str
    match: RecipeMatch
    missing_ingredients: list[MissingIngredientRef]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> MyRecipeItem:
        """my_recipe_candidates 쿼리 행을 명세 응답 모양으로 바꿉니다.

        asyncpg 는 jsonb 를 기본 설정에서 str 로 주기 때문에 여기서 파싱합니다.
        """
        raw = row["missing_ingredients"]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        missing = [MissingIngredientRef.model_validate(m) for m in parsed]
        match = RecipeMatch(
            required_ingredients=row["required_count"],
            available_ingredients=row["available_count"],
            missing_ingredients=row["missing_count"],
            match_rate=float(row["match_rate"]),
        )
        return cls(
            recipe_id=row["recipe_id"],
            name=row["name"],
            difficulty=row["difficulty"],
            cook_time_min=row["cook_time_min"],
            servings=row["servings"],
            recommendation_reason=_build_reason(match, missing),
            match=match,
            missing_ingredients=missing,
        )


def _build_reason(match: RecipeMatch, missing: list[MissingIngredientRef]) -> str:
    """규칙 기반 추천 이유 문장. 생성 방식(규칙 vs LLM)이 확정되면 여기만 바꿉니다."""
    if match.missing_ingredients == 0:
        return "필수 재료를 모두 보유하고 있어요"
    names = ", ".join(m.name for m in missing[:2])
    if match.missing_ingredients == 1:
        return f"{names}만 있으면 만들 수 있어요"
    return (
        f"필수 재료 {match.available_ingredients}/{match.required_ingredients}개 보유, "
        f"{names} 등 {match.missing_ingredients}개가 더 필요해요"
    )


class MyRecipeListResponse(BaseModel):
    """My냉장고 기반 레시피 추천 목록 (명세 21장 data)."""

    items: list[MyRecipeItem]
