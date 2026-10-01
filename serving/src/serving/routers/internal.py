"""BE 서비스가 부르는 내부 엔드포인트. 사용자 JWT 가 아니라 관리자 role 의 JWT 로 호출합니다.

`docs/api/be-sync.md` 3절. 배송완료 Admin API 가 주문 품목을 그 사용자의 My냉장고에 넣습니다.
사용자 본인용 CRUD(`routers/fridge.py`)와 달리 대상 사용자는 요청 본문에서 받고, 호출자 식별은
`AdminUserId` 가 합니다.
"""

from __future__ import annotations

from fastapi import APIRouter

from serving import app_user_sql, fridge_sql
from serving.auth import AdminUserId
from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.schemas import (
    FridgeBulkUpsertRequest,
    FridgeBulkUpsertResponse,
    FridgeItemCreate,
    FridgeSkippedItem,
    FridgeUpsertedItem,
)

router = APIRouter(prefix="/internal/fridge", tags=["internal"])

PRODUCT_NOT_FOUND = "상품을 찾을 수 없습니다."
NO_PRIMARY_INGREDIENT = "재료 정보가 연결되지 않은 상품이라 담을 수 없습니다."


@router.post("/items", response_model=ApiResponse[FridgeBulkUpsertResponse])
async def upsert_fridge_items(
    pool: PoolDep, admin_id: AdminUserId, body: FridgeBulkUpsertRequest
) -> ApiResponse[FridgeBulkUpsertResponse]:
    """주문 품목을 사용자의 My냉장고에 upsert 합니다 (배송완료 시 BE 가 호출).

    - 없는 상품은 새로 담고, 이미 담긴 상품은 **수량을 더합니다**. unit 은 새 값, expires_at 은 보낸 경우만 바꿉니다.
    - 사용자가 실제로 산 상품이라 판매 중지(is_active=false) 여부는 보지 않습니다.
    - 없는 상품·재료 미연결 상품은 거절하지 않고 ``skipped`` 에 사유와 함께 냅니다. 나머지는 처리합니다.
    - 품목 전체가 한 트랜잭션입니다. 중간에 DB 오류가 나면 전부 되돌리고 500 이라 BE 가 통째로 재시도합니다.
      성공(200) 뒤 재시도하면 수량이 두 번 더해지므로 BE 는 5xx·네트워크 오류에만 재시도합니다.
    """
    upserted: list[FridgeUpsertedItem] = []
    skipped: list[FridgeSkippedItem] = []
    async with pool.acquire() as conn, conn.transaction():
        # BE 사용자는 처음 담을 때 app_user 에 없습니다. 등록하지 않으면 FK 위반으로 500 입니다(app_user_sql).
        await conn.execute(app_user_sql.ENSURE_USER, body.user_id)
        # 같은 사용자의 요청 두 개가 서로 다른 순서로 잠금을 잡아 교착되지 않게 상품 id 순으로 처리합니다.
        for item in sorted(body.items, key=lambda i: i.product_id):
            result = await _upsert_one(conn, body.user_id, item)
            if isinstance(result, FridgeSkippedItem):
                skipped.append(result)
            else:
                upserted.append(result)
    return ApiResponse.success(FridgeBulkUpsertResponse(user_id=body.user_id, items=upserted, skipped=skipped))


async def _upsert_one(conn: object, user_id: int, item: FridgeItemCreate) -> FridgeUpsertedItem | FridgeSkippedItem:
    """품목 하나를 잠금 아래에서 담거나 수량을 더합니다. 사용자 CRUD(POST)와 같은 잠금·중복 확인 순서입니다."""
    # asyncpg.Connection 과 테스트의 가짜 연결이 같은 메서드를 갖습니다. 타입은 duck typing 으로 둡니다.
    execute, fetchrow, fetch = conn.execute, conn.fetchrow, conn.fetch  # type: ignore[attr-defined]
    if await fetchrow(fridge_sql.PRODUCT_EXISTS, item.product_id) is None:
        return FridgeSkippedItem(product_id=item.product_id, reason=PRODUCT_NOT_FOUND)
    await execute(fridge_sql.LOCK_ITEM, user_id, item.product_id)
    expires_provided = "expires_at" in item.model_fields_set
    if await fetchrow(fridge_sql.EXISTS_ITEM, user_id, item.product_id) is not None:
        rows = await fetch(
            fridge_sql.ADD_QUANTITY,
            user_id,
            item.product_id,
            item.quantity,
            item.unit,
            item.expires_at,
            expires_provided,
        )
        updated = rows[0]
        return FridgeUpsertedItem(
            product_id=item.product_id,
            action="updated",
            quantity=float(updated["quantity"]),
            unit=updated["unit"],
            expires_at=updated["expires_at"],
        )
    inserted = await fetch(fridge_sql.INSERT_ITEM, user_id, item.product_id, item.quantity, item.unit, item.expires_at)
    if not inserted:
        # 중복은 잠금 아래에서 위가 걸렀으므로, 0행이면 상품에 PRIMARY 재료가 없는 것입니다.
        return FridgeSkippedItem(product_id=item.product_id, reason=NO_PRIMARY_INGREDIENT)
    return FridgeUpsertedItem(
        product_id=item.product_id,
        action="inserted",
        quantity=item.quantity,
        unit=item.unit,
        expires_at=item.expires_at,
    )
