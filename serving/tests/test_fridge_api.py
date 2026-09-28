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
    """POST 가 부르는 문장(상품 확인, 중복 확인, 삽입)에 정해진 결과를 냅니다. SQL 은 실행하지 않습니다.

    ``exists`` 는 중복 확인을 부를 때마다 앞에서 하나씩 꺼내 씁니다. 동시 요청이 끼어든 상황을
    "처음엔 없었는데 삽입 뒤에는 있다" 로 흉내 냅니다.
    """

    def __init__(self, *, product: dict[str, Any] | None, exists: list[bool], inserted: list[dict[str, Any]]) -> None:
        self.product = product
        self.exists = list(exists)
        self.inserted = inserted

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_PostConnection]:
        yield self

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if sql == fridge_sql.EXISTS_ITEM:
            return {"?column?": 1} if self.exists.pop(0) else None
        return self.product

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        assert sql == fridge_sql.INSERT_ITEM
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
    connection = _PostConnection(product=_product(), exists=[False], inserted=[{"ingredient_id": 12}])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 200
    assert response.json()["data"]["product_id"] == 101


async def test_post_rejects_inactive_product() -> None:
    """판매 중지 상품은 추천·구매 경로와 같은 규칙으로 새로 담지 못합니다 (H5)."""
    connection = _PostConnection(product=_product(is_active=False), exists=[False], inserted=[{"ingredient_id": 12}])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 409
    assert response.json()["message"] == INACTIVE_PRODUCT


async def test_post_race_reports_duplicate() -> None:
    """두 번 누른 요청이 중복 확인 뒤에 끼어들면 삽입이 0행입니다. 재료 미연결이 아니라 중복으로 답합니다 (H6).

    예전에는 0행을 전부 "재료 정보가 연결되지 않은 상품" 으로 읽어, 다시 눌러도 같은 틀린 안내가 나왔습니다.
    """
    connection = _PostConnection(product=_product(), exists=[False, True], inserted=[])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 409
    assert response.json()["message"] == DUPLICATE_ITEM


async def test_post_without_primary_ingredient_is_rejected() -> None:
    """PRIMARY 재료가 없는 상품은 삽입이 0행이고 다시 확인해도 없으므로 재료 미연결로 답합니다."""
    connection = _PostConnection(product=_product(), exists=[False, False], inserted=[])
    async with _client_for(connection) as client:
        response = await client.post(PATH, headers=USER, json=BODY)

    assert response.status_code == 409
    assert response.json()["message"] == NO_PRIMARY_INGREDIENT


def test_fridge_item_maps_row_to_spec_shape() -> None:
    """조회 행을 명세 20장 모양으로 바꿉니다. jsonb 재료 배열도 파싱합니다."""
    item = FridgeItem.from_row(_fridge_row())

    assert item.product.product_id == 101
    assert item.product.name == "한돈 앞다리살 500g"
    assert [i.name for i in item.ingredients] == ["돼지고기"]
    assert item.quantity == 500.0
    assert item.is_expired is False
    assert "user_id" not in item.model_dump()
