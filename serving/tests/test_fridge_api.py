"""My냉장고 CRUD 테스트. DB 없이 돕니다.

명세 v0.2 20/23장 대응. 실제 user_fridge 스키마에는 fridge_item_id 가 없어
(PK 가 ingredient_id + user_id + product_id 복합키) 수정·삭제 키로 product_id 를
씁니다. 모든 엔드포인트는 X-User-Id 필수입니다.
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient

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


def test_fridge_item_maps_row_to_spec_shape() -> None:
    """조회 행을 명세 20장 모양으로 바꿉니다. jsonb 재료 배열도 파싱합니다."""
    item = FridgeItem.from_row(_fridge_row())

    assert item.product.product_id == 101
    assert item.product.name == "한돈 앞다리살 500g"
    assert [i.name for i in item.ingredients] == ["돼지고기"]
    assert item.quantity == 500.0
    assert item.is_expired is False
    assert "user_id" not in item.model_dump()
