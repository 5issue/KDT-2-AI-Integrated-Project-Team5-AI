"""추천 엔드포인트.

SQL 은 `recsys_sql` 카탈로그에 있습니다. 이 폴더는 .sql 파일을 갖지 않습니다.
`build_query` 가 이름과 파라미터를 받아 asyncpg 형식으로 바꿔 줍니다.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, Query

from serving.dependencies import PoolDep
from serving.queries import build_query
from serving.schemas import (
    RecipeRecommendation,
    RecommendationListResponse,
    ReorderCandidate,
    ReorderListResponse,
)

router = APIRouter(prefix="/users/{user_id}", tags=["recommendations"])

UserIdPath = Path(description="사용자 id", ge=1)


@router.get("/recipe-recommendations", response_model=RecommendationListResponse)
async def read_recipe_recommendations(
    pool: PoolDep,
    user_id: int = UserIdPath,
    min_coverage: float = Query(default=0.5, ge=0.0, le=1.0, description="필수 재료 커버리지 하한"),
    limit: int = Query(default=10, ge=1, le=50, description="가져올 개수"),
) -> RecommendationListResponse:
    """냉장고 재료로 만들 수 있는 레시피를 추천합니다."""
    sql, args = build_query(
        "fridge_recipe_match",
        {"user_id": user_id, "min_coverage": min_coverage, "max_results": limit},
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [RecipeRecommendation.model_validate(dict(row)) for row in rows]
    return RecommendationListResponse(user_id=user_id, count=len(items), items=items)


@router.get("/reorder-candidates", response_model=ReorderListResponse)
async def read_reorder_candidates(
    pool: PoolDep,
    user_id: int = UserIdPath,
    days_since: int = Query(default=30, ge=1, le=365, description="마지막 구매 후 경과일 하한"),
    limit: int = Query(default=10, ge=1, le=50, description="가져올 개수"),
) -> ReorderListResponse:
    """다 떨어졌을 때가 된 단골 상품을 추천합니다."""
    sql, args = build_query(
        "reorder_candidates",
        {"user_id": user_id, "days_since": days_since, "max_results": limit},
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [ReorderCandidate.model_validate(dict(row)) for row in rows]
    return ReorderListResponse(user_id=user_id, count=len(items), items=items)
