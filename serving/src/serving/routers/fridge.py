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

from serving import fridge_sql
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

DUPLICATE_ITEM = "이미 냉장고에 담긴 상품입니다."
NO_PRIMARY_INGREDIENT = "재료 정보가 연결되지 않은 상품이라 담을 수 없습니다."
INACTIVE_PRODUCT = "판매가 중지된 상품이라 담을 수 없습니다."


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
    """품목을 추가합니다. 이미 담긴 상품, 판매 중지 상품, 재료 미연결 상품은 409 로 거절합니다.

    409 는 FE 가 ``message`` 로 분기하는 계약이라, 여러 경우에 걸치면 이 순서로 하나만 냅니다.
    이미 담긴 상품이 나중에 판매 중지되어도 "이미 담긴 상품" 입니다. 품목은 냉장고에 그대로 보이기 때문입니다.
    """
    async with pool.acquire() as conn:
        detail_sql, detail_args = build_query("product_detail", {"product_id": body.product_id})
        product = await conn.fetchrow(detail_sql, *detail_args)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="상품을 찾을 수 없습니다.")
        # 삽입 전에 중복을 먼저 봅니다. 삽입의 ON CONFLICT 로만 가리면, PRIMARY 재료가 나중에 늘어난 상품은
        # 새 재료 행만 들어가 성공(200)으로 답하고, 한 품목이 수량이 다른 두 줄이 됩니다.
        if await conn.fetchrow(fridge_sql.EXISTS_ITEM, user_id, body.product_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_ITEM)
        if not product["is_active"]:
            # 판매 중지 상품은 추천·구매 경로(missing_products, bubble_products)에서 빠집니다. 새로 담는 것도
            # 같은 규칙으로 막습니다. 상세 조회와 이미 담긴 품목은 그대로 보입니다.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=INACTIVE_PRODUCT)
        inserted = await conn.fetch(
            fridge_sql.INSERT_ITEM, user_id, body.product_id, body.quantity, body.unit, body.expires_at
        )
        if not inserted:
            # 0행은 두 경우입니다. 같은 상품을 담는 요청이 위 중복 확인 뒤에 끼어들어 먼저 들어갔거나
            # (ON CONFLICT DO NOTHING), 상품에 PRIMARY 재료가 없거나. 다시 확인해야 가를 수 있습니다.
            # ON CONFLICT 는 충돌한 쪽이 커밋할 때까지 기다리므로 이 시점에는 그 행이 보입니다.
            if await conn.fetchrow(fridge_sql.EXISTS_ITEM, user_id, body.product_id) is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_ITEM)
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_PRIMARY_INGREDIENT)

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
    expires_provided = "expires_at" in body.model_fields_set
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            fridge_sql.UPDATE_ITEM,
            user_id,
            product_id,
            body.quantity,
            body.unit,
            body.expires_at,
            expires_provided,
        )

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="냉장고에 없는 품목입니다.")
    updated = rows[0]
    return ApiResponse.success(
        FridgeItemSummary(
            product_id=product_id,
            quantity=float(updated["quantity"]),
            unit=updated["unit"],
            expires_at=updated["expires_at"],
        )
    )


@router.delete("/{product_id}", response_model=ApiResponse[None])
async def delete_fridge_item(
    pool: PoolDep, user_id: CurrentUserId, product_id: int = ProductIdPath
) -> ApiResponse[None]:
    """품목을 삭제합니다. 합의대로 HTTP 200 + envelope(data null) 로 답합니다."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(fridge_sql.DELETE_ITEM, user_id, product_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="냉장고에 없는 품목입니다.")
    return ApiResponse.success()
