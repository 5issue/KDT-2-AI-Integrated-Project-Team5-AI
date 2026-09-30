"""My냉장고 CRUD 테스트. DB 없이 돕니다.

명세 v0.2 20/23장 대응. 실제 user_fridge 스키마에는 fridge_item_id 가 없어
(PK 가 ingredient_id + user_id + product_id 복합키) 수정·삭제 키로 product_id 를
씁니다. 모든 엔드포인트는 X-User-Id 필수입니다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from serving import fridge_sql
from serving.app import create_app
from serving.config import Settings
from serving.routers.fridge import DUPLICATE_ITEM, INACTIVE_PRODUCT, NO_PRIMARY_INGREDIENT
from serving.schemas import FridgeItem

PATH = "/api/v1/users/me/fridge"
USER = {"X-User-Id": "1"}
BODY = {"product_id": 101, "quantity": 500, "unit": "g", "expires_at": "2026-09-30T00:00:00Z"}


def _fridge_row() -> dict[str, Any]:
    """my_fridge_items 쿼리가 내는 행 모양."""
    return {
        "user_id": 1,
        "product_id": 101,
        "product_name": "한돈 앞다리살 500g",
        "storage_type": "냉장",
        "weight_g": 500,
        "image_url": "https://example.com/p.jpg",
        "quantity": 500,
        "unit": "g",
        "expires_at": None,
        "is_expired": False,
        "ingredients": json.dumps([{"ingredient_id": 12, "name": "돼지고기"}]),
    }


async def test_all_fridge_routes_require_user(validating_client: AsyncClient) -> None:
    """X-User-Id 없으면 전부 401 UNAUTHORIZED envelope 입니다."""
    checks = [
        ("GET", PATH, None),
        ("POST", PATH, BODY),
        ("PATCH", f"{PATH}/101", {"quantity": 300}),
        ("DELETE", f"{PATH}/101", None),
    ]
    for method, path, body in checks:
        response = await validating_client.request(method, path, json=body)
        assert response.status_code == 401, (method, path)
        assert response.json()["error"] == "UNAUTHORIZED", (method, path)


async def test_fridge_routes_exist(offline_client: AsyncClient) -> None:
    """헤더가 유효하면 라우팅되고, DB 미연결이면 503 envelope 입니다."""
    checks = [
        ("GET", PATH, None),
        ("POST", PATH, BODY),
        ("PATCH", f"{PATH}/101", {"quantity": 300}),
        ("DELETE", f"{PATH}/101", None),
    ]
    for method, path, body in checks:
        response = await offline_client.request(method, path, headers=USER, json=body)
        assert response.status_code == 503, (method, path)
        assert response.json()["error"] == "SERVICE_UNAVAILABLE", (method, path)


async def test_post_body_is_validated(validating_client: AsyncClient) -> None:
    """수량 0 이하, product_id 누락 등은 422 INVALID_INPUT_VALUE 입니다."""
    bad_bodies = [
        {},
        {"product_id": 101, "quantity": 0, "unit": "g"},
        {"product_id": 101, "quantity": -1, "unit": "g"},
        {"product_id": 101, "quantity": 500, "unit": ""},
        {"quantity": 500, "unit": "g"},
    ]
    for body in bad_bodies:
        response = await validating_client.post(PATH, headers=USER, json=body)
        assert response.status_code == 422, body
        assert response.json()["error"] == "INVALID_INPUT_VALUE", body


async def test_patch_requires_at_least_one_field(validating_client: AsyncClient) -> None:
    """빈 body 로 PATCH 하면 422 입니다."""
    response = await validating_client.patch(f"{PATH}/101", headers=USER, json={})

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_INPUT_VALUE"


class _PostConnection:
    """POST 가 부르는 문장(상품 확인, 잠금, 중복 확인, 삽입)에 정해진 결과를 냅니다. SQL 은 실행하지 않습니다.

    부른 순서를 ``calls`` 에 남깁니다. 중복 확인과 삽입이 잠금을 잡은 트랜잭션 안에서 도는지 봅니다.
    잠금이 실제로 같은 사용자·상품의 담기를 줄 세우는지는 통합 테스트가 실제 DB 로 봅니다.
    """

    def __init__(self, *, product: dict[str, Any] | None, exists: bool, inserted: list[dict[str, Any]]) -> None:
        self.product = product
        self.exists = exists
        self.inserted = inserted
        self.calls: list[str] = []

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_PostConnection]:
        yield self

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.calls.append("begin")
        try:
            yield
        except BaseException:
            self.calls.append("rollback")
            raise
        self.calls.append("commit")

    async def execute(self, sql: str, *args: Any) -> str:
        assert sql == fridge_sql.LOCK_ITEM
        assert args == (1, 101), "잠금 키가 사용자·상품이 아닙니다"
        self.calls.append("lock")
        return "SELECT 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if sql == fridge_sql.EXISTS_ITEM:
            self.calls.append("exists")
            return {"?column?": 1} if self.exists else None
        return self.product

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        assert sql == fridge_sql.INSERT_ITEM
        self.calls.append("insert")
        return self.inserted


def _product(*, is_active: bool = True) -> dict[str, Any]:
    return {"product_id": 101, "name": "한돈 앞다리살 500g", "is_active": is_active}


@asynccontextmanager
async def _client_for(connection: _PostConnection) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    app.state.pool = connection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_post_adds_item() -> None:
    """정상 상품은 담기고 요청 값을 그대로 돌려줍니다."""
    connection = _PostConnection(product=_product(), exists=False, inserted=[{"ingredient_id": 12}])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 200
    assert response.json()["data"]["product_id"] == 101


async def test_post_checks_and_inserts_under_the_item_lock() -> None:
    """중복 확인과 삽입은 사용자·상품 잠금을 먼저 잡은 한 트랜잭션에서 돕니다 (PR #42 리뷰).

    잠금 없이 두 번 누른 요청이 둘 다 중복 확인을 통과하면, 그 사이 PRIMARY 재료가 바뀐 상품은 뒤 요청도
    새 재료 행만 넣고 200 으로 답해 한 품목이 두 줄이 됩니다. 거절되면 트랜잭션은 롤백됩니다.
    """
    added = _PostConnection(product=_product(), exists=False, inserted=[{"ingredient_id": 12}])
    duplicate = _PostConnection(product=_product(), exists=True, inserted=[])
    for connection, expected in ((added, 200), (duplicate, 409)):
        async with _client_for(connection) as client:
            assert (await client.post(PATH, headers=USER, json=BODY)).status_code == expected

    assert added.calls == ["begin", "lock", "exists", "insert", "commit"]
    assert duplicate.calls == ["begin", "lock", "exists", "rollback"]


async def test_post_rejects_inactive_product() -> None:
    """판매 중지 상품은 추천·구매 경로와 같은 규칙으로 새로 담지 못합니다 (H5)."""
    connection = _PostConnection(product=_product(is_active=False), exists=False, inserted=[{"ingredient_id": 12}])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 409
    assert response.json()["message"] == INACTIVE_PRODUCT


async def test_post_already_added_product_that_was_deactivated_reports_duplicate() -> None:
    """이미 담긴 상품이 나중에 판매 중지되면, 다시 담기는 "판매 중지" 가 아니라 "이미 담긴 상품" 입니다 (PR #42 리뷰).

    FE 는 409 를 message 로 분기합니다. 품목은 냉장고에 그대로 보이므로 중복 안내가 맞습니다.
    """
    connection = _PostConnection(product=_product(is_active=False), exists=True, inserted=[])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 409
    assert response.json()["message"] == DUPLICATE_ITEM


async def test_post_without_primary_ingredient_is_rejected() -> None:
    """PRIMARY 재료가 없는 상품은 삽입이 0행입니다. 중복은 잠금 아래에서 먼저 걸렀으므로 재료 미연결로 답합니다."""
    connection = _PostConnection(product=_product(), exists=False, inserted=[])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 409
    assert response.json()["message"] == NO_PRIMARY_INGREDIENT


def test_fridge_item_maps_row_to_spec_shape() -> None:
    """조회 행을 명세 20장 모양으로 바꿉니다. jsonb 재료 배열도 파싱합니다."""
    item = FridgeItem.from_row(_fridge_row())

    assert item.product.product_id == 101
    assert item.product.name == "한돈 앞다리살 500g"
    assert item.product.image_url == "https://example.com/p.jpg"
    assert [i.name for i in item.ingredients] == ["돼지고기"]
    assert item.quantity == 500.0
    assert item.is_expired is False
    assert "user_id" not in item.model_dump()


def test_fridge_item_without_product_image_is_null() -> None:
    """원천에 이미지가 없는 상품은 image_url 이 null 입니다. 화면이 placeholder 를 둡니다."""
    item = FridgeItem.from_row({**_fridge_row(), "image_url": None})

    assert item.product.image_url is None
