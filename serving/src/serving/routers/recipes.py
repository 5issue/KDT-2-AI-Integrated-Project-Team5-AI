"""레시피 엔드포인트 (명세 17/18장).

missing-ingredients 는 비로그인도 허용합니다. 보유 판정 세 갈래(고른 상품 / 냉장고 /
상비재료) 중 안 쓰는 갈래는 SQL 에 0 을 넘겨 끕니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, status

from serving.auth import OptionalUserId
from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import (
    BaseProductRef,
    MissingIngredientsResponse,
    RecipeDetailResponse,
)

router = APIRouter(prefix="/recipes", tags=["recipes"])

RecipeIdPath = Path(description="레시피 id", ge=1)


@router.get("/{recipe_id}", response_model=ApiResponse[RecipeDetailResponse])
async def read_recipe_detail(
    pool: PoolDep,
    recipe_id: int = RecipeIdPath,
) -> ApiResponse[RecipeDetailResponse]:
    """레시피 상세를 재료줄·조리 단계와 함께 냅니다."""
    sql, args = build_query("recipe_detail", {"recipe_id": recipe_id})
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="레시피를 찾을 수 없습니다.")
    return ApiResponse.success(RecipeDetailResponse.from_row(dict(row)))


@router.get(
    "/{recipe_id}/missing-ingredients",
    response_model=ApiResponse[MissingIngredientsResponse],
)
async def read_missing_ingredients(
    pool: PoolDep,
    user_id: OptionalUserId,
    recipe_id: int = RecipeIdPath,
    base_product_id: int = Query(default=0, ge=0, description="기준 상품 id. 0 이면 미사용"),
) -> ApiResponse[MissingIngredientsResponse]:
    """레시피 재료 중 기준 상품·냉장고·상비재료로 채워지지 않는 것을 계산합니다."""
    async with pool.acquire() as conn:
        base_product: BaseProductRef | None = None
        if base_product_id > 0:
            detail_sql, detail_args = build_query("product_detail", {"product_id": base_product_id})
            product_row = await conn.fetchrow(detail_sql, *detail_args)
            if product_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="기준 상품을 찾을 수 없습니다.")
            base_product = BaseProductRef(product_id=product_row["product_id"], name=product_row["name"])

        sql, args = build_query(
            "recipe_missing_ingredients",
            {"recipe_id": recipe_id, "base_product_id": base_product_id, "user_id": user_id},
        )
        rows = await conn.fetch(sql, *args)

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="레시피를 찾을 수 없습니다.")
    return ApiResponse.success(
        MissingIngredientsResponse.from_rows(
            recipe_id=recipe_id,
            base_product=base_product,
            rows=[dict(row) for row in rows],
        )
    )
