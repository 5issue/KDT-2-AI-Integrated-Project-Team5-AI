"""상품 엔드포인트 (명세 15/16/22장).

SQL 은 recsys_sql 카탈로그에서 가져옵니다. 사용자 컨텍스트가 없어 인증이 필요 없고,
값은 전부 asyncpg 위치 파라미터로 넘어갑니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, status

from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import (
    ProductDetailResponse,
    ProductRecipeItem,
    ProductRecipeListResponse,
    StorageGuideItem,
    StorageGuideResponse,
)

router = APIRouter(prefix="/products", tags=["products"])

ProductIdPath = Path(description="상품 id", ge=1)


@router.get("/{product_id}", response_model=ApiResponse[ProductDetailResponse])
async def read_product_detail(
    pool: PoolDep,
    product_id: int = ProductIdPath,
) -> ApiResponse[ProductDetailResponse]:
    """상품 상세를 구성 재료와 함께 냅니다."""
    sql, args = build_query("product_detail", {"product_id": product_id})
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="상품을 찾을 수 없습니다.")
    return ApiResponse.success(ProductDetailResponse.from_row(dict(row)))


@router.get("/{product_id}/recipes", response_model=ApiResponse[ProductRecipeListResponse])
async def read_product_recipes(
    pool: PoolDep,
    product_id: int = ProductIdPath,
    limit: int = Query(default=10, ge=1, le=50, description="가져올 개수"),
) -> ApiResponse[ProductRecipeListResponse]:
    """상품(의 재료)으로 만들 수 있는 레시피를 커버리지 요약과 함께 냅니다.

    레시피가 없는 상품은 404 가 아니라 빈 목록입니다. pagination 은 명세에서
    제외로 확정되어 skip 은 0 고정입니다.
    """
    sql, args = build_query("product_recipes", {"product_id": product_id, "max_results": limit, "skip": 0})
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [ProductRecipeItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(ProductRecipeListResponse(items=items))


@router.get("/{product_id}/storage-guide", response_model=ApiResponse[StorageGuideResponse])
async def read_storage_guide(
    pool: PoolDep,
    product_id: int = ProductIdPath,
) -> ApiResponse[StorageGuideResponse]:
    """상품의 보관 가이드를 냅니다.

    보관법은 PRIMARY 재료가 정확히 하나일 때만 존재합니다. 지침이 없으면
    (재료 미연결, PRIMARY 다중, 원천 데이터 없음) 404 로 답합니다.
    """
    sql, args = build_query("product_storage_guideline", {"product_id": product_id})
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="보관 가이드가 없습니다.")
    first = dict(rows[0])
    items = [StorageGuideItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(
        StorageGuideResponse(
            product_id=first["product_id"],
            storage_type=first["storage_type"],
            ingredient_name=first["ingredient_name"],
            items=items,
        )
    )
