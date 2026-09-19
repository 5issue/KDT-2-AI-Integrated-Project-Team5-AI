"""추천 엔드포인트.

SQL 은 `recsys_sql` 카탈로그에 있습니다. 이 폴더는 .sql 파일을 갖지 않습니다.
`build_query` 가 이름과 파라미터를 받아 asyncpg 형식으로 바꿔 줍니다.

사용자 식별은 경로 파라미터가 아니라 `auth.CurrentUserId`(X-User-Id 헤더)로 합니다.
경로의 user_id 를 그대로 믿으면 타인 데이터 조회(IDOR)가 가능하기 때문입니다.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from serving.auth import CurrentUserId
from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import MyRecipeItem, MyRecipeListResponse

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/my-recipes", response_model=ApiResponse[MyRecipeListResponse])
async def read_my_recipes(
    pool: PoolDep,
    user_id: CurrentUserId,
    min_match_rate: float = Query(default=0.5, ge=0.0, le=1.0, description="필수 재료 매칭률 하한"),
    limit: int = Query(default=10, ge=1, le=50, description="가져올 개수"),
) -> ApiResponse[MyRecipeListResponse]:
    """My냉장고 재료로 만들 수 있는 레시피를 추천합니다 (명세 21장)."""
    sql, args = build_query(
        "my_recipe_candidates",
        {"user_id": user_id, "min_match_rate": min_match_rate, "max_results": limit},
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [MyRecipeItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(MyRecipeListResponse(items=items))
