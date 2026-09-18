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


class BubbleItem(BaseModel):
    """홈 추천 버블 한 개 (명세 13장)."""

    bubble_id: str
    label: str
    description: str | None = None
    type: str = "RECIPE"
    enabled: bool

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BubbleItem:
        """bubble_candidate_counts 행을 명세 모양으로 바꿉니다.

        keyword_id 가 곧 버블 코드이고, 후보 수가 하한 미달이면 enabled=false 로
        내려서 화면이 누를 수 없게 합니다. type 은 현재 레시피 버블뿐이라 상수입니다.
        """
        return cls(
            bubble_id=row["keyword_id"],
            label=row["label"],
            description=row["description"],
            enabled=bool(row["is_servable"]),
        )


class BubbleListResponse(BaseModel):
    """홈 버블 목록 (명세 13장 data)."""

    items: list[BubbleItem]


class ProductIngredientRef(BaseModel):
    """상품 구성 재료 참조 (명세 15장 ingredients 항목)."""

    ingredient_id: int
    name: str


class ProductDetailResponse(BaseModel):
    """상품 상세 (명세 15장 data). 명세에 없는 컬럼(is_active 등)은 내지 않습니다."""

    product_id: int
    name: str
    price: float
    weight_g: int | None = None
    product_type: str | None = None
    storage_type: str | None = None
    origin_country: str | None = None
    stock_quantity: int | None = None
    ingredients: list[ProductIngredientRef]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ProductDetailResponse:
        raw = row["ingredients"]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return cls(
            product_id=row["product_id"],
            name=row["name"],
            price=float(row["price"]),
            weight_g=row["weight_g"],
            product_type=row["product_type"],
            storage_type=row["storage_type"],
            origin_country=row["origin_country"],
            stock_quantity=row["stock_quantity"],
            ingredients=[ProductIngredientRef.model_validate(i) for i in parsed],
        )


class IngredientSummary(BaseModel):
    """레시피 재료 커버리지 요약 (명세 16장 ingredient_summary)."""

    total_count: int
    matched_count: int
    missing_count: int


class ProductRecipeItem(BaseModel):
    """상품으로 만들 수 있는 레시피 한 건 (명세 16장)."""

    recipe_id: int
    name: str
    difficulty: str | None = None
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    servings: int | None = None
    cooking_method: str | None = None
    ingredient_summary: IngredientSummary

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ProductRecipeItem:
        return cls(
            recipe_id=row["recipe_id"],
            name=row["name"],
            difficulty=row["difficulty"],
            prep_time_min=row["prep_time_min"],
            cook_time_min=row["cook_time_min"],
            servings=row["servings"],
            cooking_method=row["cooking_method"],
            ingredient_summary=IngredientSummary(
                total_count=row["total_count"],
                matched_count=row["matched_count"],
                missing_count=row["missing_count"],
            ),
        )


class ProductRecipeListResponse(BaseModel):
    """상품 레시피 목록 (명세 16장 data)."""

    items: list[ProductRecipeItem]


class StorageGuideItem(BaseModel):
    """보관 가이드 한 줄. 같은 상품에 (장소, 상황)별로 여러 줄이 올 수 있습니다.

    명세 22장의 temperature_min/max, instruction_text 는 원천(FoodKeeper)에 없어
    내지 않고, tips 도 원천이 단일 텍스트라 문자열입니다. 명세 조정 협의 대상입니다.
    """

    storage_location: str
    storage_context: str | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    duration_unit: str | None = None
    duration_text: str | None = None
    tips: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> StorageGuideItem:
        return cls(
            storage_location=row["storage_location"],
            storage_context=row["storage_context"],
            duration_min=row["duration_min"],
            duration_max=row["duration_max"],
            duration_unit=row["duration_unit"],
            duration_text=row["duration_text"],
            tips=row["storage_tips"],
        )


class StorageGuideResponse(BaseModel):
    """상품 보관 가이드 (명세 22장 data)."""

    product_id: int
    storage_type: str | None = None
    ingredient_name: str | None = None
    items: list[StorageGuideItem]
