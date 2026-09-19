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
from serving.schemas import MissingProductsResponse, RecipeDetailResponse

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
    "/{recipe_id}/missing-products",
    response_model=ApiResponse[MissingProductsResponse],
)
async def read_missing_products(
    pool: PoolDep,
    user_id: OptionalUserId,
    recipe_id: int = RecipeIdPath,
    base_product_id: int = Query(default=0, ge=0, description="기준 상품 id. 0 이면 미사용"),
    max_per_ingredient: int = Query(default=3, ge=1, le=10, description="재료당 추천 상품 수"),
) -> ApiResponse[MissingProductsResponse]:
    """부족 재료와 재료별 추천 상품을 냅니다 (18장 + 30장 통일).

    부족 재료가 없으면 빈 목록 성공 응답입니다. 그래서 "레시피 없음" 과 구분하기 위해
    상세 쿼리로 존재를 먼저 확인하고 404 를 냅니다.
    """
    async with pool.acquire() as conn:
        detail_sql, detail_args = build_query("recipe_detail", {"recipe_id": recipe_id})
        if await conn.fetchrow(detail_sql, *detail_args) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="레시피를 찾을 수 없습니다.")

        sql, args = build_query(
            "missing_products",
            {
                "user_id": user_id,
                "recipe_id": recipe_id,
                "base_product_id": base_product_id,
                "max_per_ingredient": max_per_ingredient,
            },
        )
        rows = await conn.fetch(sql, *args)

    return ApiResponse.success(MissingProductsResponse.from_rows(recipe_id=recipe_id, rows=[dict(row) for row in rows]))
