"""찜한 레시피 / 최근 본 레시피 (My 레시피 화면 하단 두 줄). 모든 엔드포인트는 사용자 식별이 필요합니다.

조회는 카탈로그(user_favorite_recipes, user_recent_recipes)를 쓰고, 쓰기는 카탈로그가
읽기 전용 원칙이라 `user_recipe_sql` 의 asyncpg 상수로 둡니다.

레시피 존재 확인은 별도 SELECT 대신 FK 위반으로 받습니다. 어느 FK 가 깨졌는지는
제약 이름으로 가르고 404 로 냅니다. 사용자는 쓰기 직전에 app_user 에 등록하므로(`app_user_sql`)
정상 흐름에서 사용자 FK 는 깨지지 않습니다.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query, status

from serving import app_user_sql, user_recipe_sql
from serving.auth import CurrentUserId
from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import (
    FavoriteRecipeItem,
    FavoriteRecipeListResponse,
    FavoriteRecipeSummary,
    RecentRecipeDeleteRequest,
    RecentRecipeDeleteResponse,
    RecentRecipeItem,
    RecentRecipeListResponse,
    RecentRecipeSummary,
)

router = APIRouter(prefix="/users/me", tags=["user-recipes"])

RecipeIdPath = Path(description="레시피 id", ge=1)

# 사용자당 보관하는 조회 기록 상한. 화면은 최대 50건만 요청할 수 있으므로 그보다 넉넉히 둡니다.
RECENT_KEEP = 100


def _not_found_from_fk(exc: asyncpg.ForeignKeyViolationError) -> HTTPException:
    """FK 위반을 404 로 바꿉니다. 어느 쪽이 없는지는 제약 이름으로 가릅니다."""
    # asyncpg 는 constraint_name 을 런타임 속성으로만 주고 스텁에는 없습니다.
    constraint = str(getattr(exc, "constraint_name", None) or "")
    if constraint.startswith("fk_app_user_"):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="레시피를 찾을 수 없습니다.")


@router.get("/favorite-recipes", response_model=ApiResponse[FavoriteRecipeListResponse])
async def read_favorite_recipes(
    pool: PoolDep,
    user_id: CurrentUserId,
    limit: int = Query(default=50, ge=1, le=100, description="최대 건수"),
) -> ApiResponse[FavoriteRecipeListResponse]:
    """찜한 레시피를 최근에 찜한 순으로 냅니다."""
    sql, args = build_query("user_favorite_recipes", {"user_id": user_id, "max_results": limit})
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [FavoriteRecipeItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(FavoriteRecipeListResponse(items=items))


@router.post("/favorite-recipes/{recipe_id}", response_model=ApiResponse[FavoriteRecipeSummary])
async def create_favorite_recipe(
    pool: PoolDep, user_id: CurrentUserId, recipe_id: int = RecipeIdPath
) -> ApiResponse[FavoriteRecipeSummary]:
    """레시피를 찜합니다. 없는 레시피는 404, 이미 찜한 레시피는 409 입니다."""
    async with pool.acquire() as conn:
        # BE 사용자는 처음 찜할 때 app_user 에 없습니다. 등록하지 않으면 사용자 FK 위반으로 404 가 됩니다.
        await conn.execute(app_user_sql.ENSURE_USER, user_id)
        try:
            row = await conn.fetchrow(user_recipe_sql.INSERT_FAVORITE, user_id, recipe_id)
        except asyncpg.ForeignKeyViolationError as exc:
            raise _not_found_from_fk(exc) from exc

    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 찜한 레시피입니다.")
    return ApiResponse.success(FavoriteRecipeSummary(recipe_id=recipe_id, favorited_at=row["created_at"]))


@router.delete("/favorite-recipes/{recipe_id}", response_model=ApiResponse[None])
async def delete_favorite_recipe(
    pool: PoolDep, user_id: CurrentUserId, recipe_id: int = RecipeIdPath
) -> ApiResponse[None]:
    """찜을 취소합니다. 냉장고 DELETE 와 같이 HTTP 200 + envelope(data null) 입니다."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(user_recipe_sql.DELETE_FAVORITE, user_id, recipe_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="찜하지 않은 레시피입니다.")
    return ApiResponse.success()


@router.get("/recent-recipes", response_model=ApiResponse[RecentRecipeListResponse])
async def read_recent_recipes(
    pool: PoolDep,
    user_id: CurrentUserId,
    limit: int = Query(default=10, ge=1, le=50, description="최대 건수"),
) -> ApiResponse[RecentRecipeListResponse]:
    """최근 본 레시피를 마지막으로 본 순으로 냅니다. 같은 레시피는 한 번만 나옵니다."""
    sql, args = build_query("user_recent_recipes", {"user_id": user_id, "max_results": limit})
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [RecentRecipeItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(RecentRecipeListResponse(items=items))


@router.post("/recent-recipes/{recipe_id}", response_model=ApiResponse[RecentRecipeSummary])
async def record_recent_recipe(
    pool: PoolDep, user_id: CurrentUserId, recipe_id: int = RecipeIdPath
) -> ApiResponse[RecentRecipeSummary]:
    """레시피 조회를 기록합니다. 다시 보면 행이 늘지 않고 시각만 갱신되며, 없는 레시피는 404 입니다.

    FE 가 상세 화면 진입 시 호출합니다. 상세 GET 에 쓰기를 숨기지 않은 것은 조회와 기록을
    분리해 두어야 비로그인·프리페치 요청이 기록을 오염시키지 않기 때문입니다.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(app_user_sql.ENSURE_USER, user_id)
            try:
                row = await conn.fetchrow(user_recipe_sql.UPSERT_VIEW, user_id, recipe_id)
            except asyncpg.ForeignKeyViolationError as exc:
                raise _not_found_from_fk(exc) from exc
            await conn.execute(user_recipe_sql.TRIM_VIEWS, user_id, RECENT_KEEP)

    assert row is not None  # UPSERT 는 항상 한 행을 돌려줍니다.
    return ApiResponse.success(RecentRecipeSummary(recipe_id=recipe_id, viewed_at=row["viewed_at"]))


@router.delete("/recent-recipes", response_model=ApiResponse[RecentRecipeDeleteResponse])
async def delete_recent_recipes(
    pool: PoolDep, user_id: CurrentUserId, body: RecentRecipeDeleteRequest
) -> ApiResponse[RecentRecipeDeleteResponse]:
    """최근 본 레시피를 골라 지웁니다. 화면의 "전체선택 -> 선택삭제" 가 체크된 id 를 한 번에 보냅니다.

    기록에 없는 id 는 404 가 아니라 건너뜁니다. 목록을 본 뒤 삭제하기까지 다른 기기에서 지워졌을
    수 있고, 화면은 "몇 건이 지워졌는지" 만 알면 되기 때문입니다. 중복 id 는 한 번만 셉니다.
    """
    async with pool.acquire() as conn:
        deleted = await conn.fetch(user_recipe_sql.DELETE_VIEWS, user_id, body.recipe_ids)

    return ApiResponse.success(RecentRecipeDeleteResponse(deleted_count=len(deleted)))
