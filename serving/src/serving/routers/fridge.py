"""My냉장고 CRUD (명세 20/23장). 모든 엔드포인트는 사용자 식별이 필요합니다.

조회는 카탈로그(my_fridge_items)를 쓰고, 쓰기 3종은 카탈로그가 읽기 전용 원칙이라
여기 asyncpg 위치 파라미터 SQL 상수로 둡니다. 모든 문장에 user_id 조건을 강제해
타인 품목 접근(IDOR)을 막고, 없는 품목은 존재 여부를 숨기기 위해 404 로 통일합니다.

user_fridge 의 PK 는 (ingredient_id, user_id, product_id) 복합키라 fridge_item_id
가 없습니다. 품목의 키는 product_id 이고, 한 상품의 PRIMARY 재료 수만큼 행이
생기지만 API 관점에서는 상품 하나가 품목 한 칸입니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, status

from serving.auth import CurrentUserId
from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import (
    FridgeItem,
    FridgeItemCreate,
    FridgeItemSummary,
    FridgeItemUpdate,
    FridgeListResponse,
)

router = APIRouter(prefix="/users/me/fridge", tags=["fridge"])

ProductIdPath = Path(description="품목의 상품 id", ge=1)

# 상품의 PRIMARY 재료 수만큼 행을 만듭니다. 재료가 연결되지 않은 상품은 0행이라
# 담기지 않습니다 (ingredient_id 가 NOT NULL 이라 담을 방법이 없습니다).
_INSERT_ITEM = """
INSERT INTO user_fridge (ingredient_id, user_id, product_id, quantity, unit, expires_at)
SELECT pi.ingredient_id, $1, $2, $3, $4, $5
FROM product_ingredient pi
WHERE pi.product_id = $2
  AND pi.role = 'PRIMARY'
ON CONFLICT DO NOTHING
RETURNING ingredient_id
"""

_EXISTS_ITEM = "SELECT 1 FROM user_fridge WHERE user_id = $1 AND product_id = $2 LIMIT 1"

_SELECT_ITEM = """
SELECT quantity, unit, expires_at
FROM user_fridge
WHERE user_id = $1 AND product_id = $2
LIMIT 1
"""

_UPDATE_ITEM = """
UPDATE user_fridge
SET quantity = $3, unit = $4, expires_at = $5
WHERE user_id = $1 AND product_id = $2
RETURNING product_id
"""

_DELETE_ITEM = "DELETE FROM user_fridge WHERE user_id = $1 AND product_id = $2 RETURNING product_id"


@router.get("", response_model=ApiResponse[FridgeListResponse])
async def read_fridge_items(pool: PoolDep, user_id: CurrentUserId) -> ApiResponse[FridgeListResponse]:
    """냉장고 품목을 상품 단위로 냅니다. 기한 지난 것도 is_expired 로 구분해 냅니다."""
    sql, args = build_query("my_fridge_items", {"user_id": user_id})
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [FridgeItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(FridgeListResponse(items=items))


@router.post("", response_model=ApiResponse[FridgeItemSummary])
async def create_fridge_item(
    pool: PoolDep, user_id: CurrentUserId, body: FridgeItemCreate
) -> ApiResponse[FridgeItemSummary]:
    """품목을 추가합니다. 이미 담긴 상품(409), 재료 미연결 상품(409)은 거절합니다."""
    async with pool.acquire() as conn:
        detail_sql, detail_args = build_query("product_detail", {"product_id": body.product_id})
        if await conn.fetchrow(detail_sql, *detail_args) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="상품을 찾을 수 없습니다.")
        if await conn.fetchrow(_EXISTS_ITEM, user_id, body.product_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 냉장고에 담긴 상품입니다.")
        inserted = await conn.fetch(_INSERT_ITEM, user_id, body.product_id, body.quantity, body.unit, body.expires_at)
        if not inserted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="재료 정보가 연결되지 않은 상품이라 담을 수 없습니다.",
            )

    return ApiResponse.success(
        FridgeItemSummary(
            product_id=body.product_id,
            quantity=body.quantity,
            unit=body.unit,
            expires_at=body.expires_at,
        )
    )


@router.patch("/{product_id}", response_model=ApiResponse[FridgeItemSummary])
async def update_fridge_item(
    pool: PoolDep,
    user_id: CurrentUserId,
    body: FridgeItemUpdate,
    product_id: int = ProductIdPath,
) -> ApiResponse[FridgeItemSummary]:
    """품목을 부분 수정합니다. 보내지 않은 필드는 그대로 둡니다."""
    async with pool.acquire() as conn:
        current = await conn.fetchrow(_SELECT_ITEM, user_id, product_id)
        if current is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="냉장고에 없는 품목입니다.")
        # quantity/unit 은 NOT NULL 이라 null 은 "미변경"으로 봅니다.
        # expires_at 은 null 이 유효한 값(기한 없음)이라 필드 전송 여부로 구분합니다.
        quantity = body.quantity if body.quantity is not None else float(current["quantity"])
        unit = body.unit if body.unit is not None else current["unit"]
        expires_at = body.expires_at if "expires_at" in body.model_fields_set else current["expires_at"]
        await conn.fetch(_UPDATE_ITEM, user_id, product_id, quantity, unit, expires_at)

    return ApiResponse.success(
        FridgeItemSummary(product_id=product_id, quantity=quantity, unit=unit, expires_at=expires_at)
    )


@router.delete("/{product_id}", response_model=ApiResponse[None])
async def delete_fridge_item(
    pool: PoolDep, user_id: CurrentUserId, product_id: int = ProductIdPath
) -> ApiResponse[None]:
    """품목을 삭제합니다. 합의대로 HTTP 200 + envelope(data null) 로 답합니다."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(_DELETE_ITEM, user_id, product_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="냉장고에 없는 품목입니다.")
    return ApiResponse.success()
