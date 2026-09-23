"""추천 엔드포인트.

SQL 은 `recsys_sql` 카탈로그에 있습니다. 이 폴더는 .sql 파일을 갖지 않습니다.
`build_query` 가 이름과 파라미터를 받아 asyncpg 형식으로 바꿔 줍니다.

사용자 식별은 경로 파라미터가 아니라 `auth.CurrentUserId`(X-User-Id 헤더)로 합니다.
경로의 user_id 를 그대로 믿으면 타인 데이터 조회(IDOR)가 가능하기 때문입니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from serving.auth import CurrentUserId
from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import (
    BubbleProductsResponse,
    MyRecipeItem,
    MyRecipeListResponse,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# 필수 재료의 절반 이상을 보유한 레시피만 추천합니다. FE 합의로 파라미터 대신
# 서버 고정값입니다 (조절 UI 가 생기면 쿼리 파라미터로 다시 엽니다).
MIN_MATCH_RATE = 0.5


@router.get("/my-recipes", response_model=ApiResponse[MyRecipeListResponse])
async def read_my_recipes(
    pool: PoolDep,
    user_id: CurrentUserId,
    limit: int = Query(default=10, ge=1, le=50, description="가져올 개수"),
) -> ApiResponse[MyRecipeListResponse]:
    """My냉장고 재료로 만들 수 있는 레시피를 추천합니다 (명세 21장)."""
    sql, args = build_query(
        "my_recipe_candidates",
        {"user_id": user_id, "min_match_rate": MIN_MATCH_RATE, "max_results": limit},
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [MyRecipeItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(MyRecipeListResponse(items=items))


@router.get("/products", response_model=ApiResponse[BubbleProductsResponse])
async def read_bubble_products(
    pool: PoolDep,
    bubble_id: str = Query(min_length=1, max_length=50, description="버블 keyword_id"),
    limit: int = Query(default=10, ge=1, le=50, description="가져올 개수"),
) -> ApiResponse[BubbleProductsResponse]:
    """버블을 눌렀을 때 나올 상품을 추천합니다 (명세 14장, RECO-01).

    버블 규칙은 전부 레시피 조건이라 상품을 바로 고르지 않고, 그 버블의 후보
    레시피들이 실제로 쓰는 필수 재료의 상품을 냅니다.
    """
    async with pool.acquire() as conn:
        bubbles_sql, bubbles_args = build_query("bubble_candidate_counts", {})
        bubble_rows = await conn.fetch(bubbles_sql, *bubbles_args)
        bubble = next((row for row in bubble_rows if row["keyword_id"] == bubble_id), None)
        if bubble is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="버블을 찾을 수 없습니다.")

        sql, args = build_query("bubble_products", {"keyword_id": bubble_id, "max_results": limit, "skip": 0})
        rows = await conn.fetch(sql, *args)

    return ApiResponse.success(
        BubbleProductsResponse.from_rows(
            bubble_id=bubble_id, bubble_label=bubble["label"], rows=[dict(row) for row in rows]
        )
    )
