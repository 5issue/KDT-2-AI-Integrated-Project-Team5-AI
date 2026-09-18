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


class RecipeIngredientLine(BaseModel):
    """레시피 재료 한 줄 (명세 17장 ingredients 항목)."""

    ingredient_id: int
    name: str
    quantity: float | None = None
    unit: str | None = None
    is_required: bool
    is_pantry: bool
    purpose: str | None = None


class RecipeStep(BaseModel):
    """조리 단계 한 줄."""

    step_no: int
    instruction: str
    image_url: str | None = None


class RecipeDetailResponse(BaseModel):
    """레시피 상세 (명세 17장 data).

    nutrition 은 원본(jsonb) 키를 그대로 냅니다. 원본마다 채워진 항목이 달라
    (protein_g, sodium_mg 등) 명세 17장의 calories/protein/carbs/fat 로 펴면
    대부분 null 이 됩니다. 키 통일은 명세 조정 협의 대상입니다.
    steps 는 명세 17장에 없지만 상세 화면이 항상 함께 쓰는 값이라 냅니다.
    """

    recipe_id: int
    name: str
    description: str | None = None
    image_url: str | None = None
    difficulty: str | None = None
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    servings: int | None = None
    cooking_method: str | None = None
    nutrition: dict[str, Any] | None = None
    ingredients: list[RecipeIngredientLine]
    steps: list[RecipeStep]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RecipeDetailResponse:
        def parse(value: Any) -> Any:
            return json.loads(value) if isinstance(value, str) else value

        return cls(
            recipe_id=row["recipe_id"],
            name=row["name"],
            description=row["description"],
            image_url=row["image_url"],
            difficulty=row["difficulty"],
            prep_time_min=row["prep_time_min"],
            cook_time_min=row["cook_time_min"],
            servings=row["servings"],
            cooking_method=row["cooking_method"],
            nutrition=parse(row["nutrition"]),
            ingredients=[RecipeIngredientLine.model_validate(i) for i in parse(row["ingredients"])],
            steps=[RecipeStep.model_validate(s) for s in parse(row["steps"])],
        )


class BaseProductRef(BaseModel):
    """부족 재료 계산의 기준 상품 (명세 18장 base_product)."""

    product_id: int
    name: str


class IngredientAvailability(BaseModel):
    """필수 재료 보유 집계 (명세 18장 ingredients 객체)."""

    total: int
    available: int
    missing: int


class MissingIngredientItem(BaseModel):
    """부족 재료 한 건 (명세 18장 missing_items 항목)."""

    ingredient_id: int
    name: str
    quantity: float | None = None
    unit: str | None = None
    status: str


class MissingIngredientsResponse(BaseModel):
    """부족 재료 계산 결과 (명세 18장 data).

    집계와 missing_items 는 필수 재료(is_required) 기준입니다. SQL 은 선택 재료와
    BASE/IN_FRIDGE/PANTRY 상태까지 내므로, 화면이 더 필요해지면 여기서 넓힙니다.
    """

    recipe_id: int
    base_product: BaseProductRef | None = None
    ingredients: IngredientAvailability
    missing_items: list[MissingIngredientItem]

    @classmethod
    def from_rows(
        cls,
        recipe_id: int,
        base_product: BaseProductRef | None,
        rows: list[dict[str, Any]],
    ) -> MissingIngredientsResponse:
        required = [r for r in rows if r["is_required"]]
        missing = [r for r in required if r["status"] == "MISSING"]
        return cls(
            recipe_id=recipe_id,
            base_product=base_product,
            ingredients=IngredientAvailability(
                total=len(required),
                available=len(required) - len(missing),
                missing=len(missing),
            ),
            missing_items=[
                MissingIngredientItem(
                    ingredient_id=r["ingredient_id"],
                    name=r["ingredient_name"],
                    quantity=float(r["quantity"]) if r["quantity"] is not None else None,
                    unit=r["unit"],
                    status=r["status"],
                )
                for r in missing
            ],
        )
