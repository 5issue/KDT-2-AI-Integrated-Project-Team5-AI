"""bigint 를 넘는 id 는 DB 에 닿기 전에 422(사용자 id 는 401)로 막습니다. DB 없이 돕니다.

경로·본문·쿼리의 id 에 상한이 없으면, 2^63 이상이 올 때 asyncpg 가 인자를 인코딩하다 DataError 를 내고
500 이 됩니다(상품·레시피 상세, 냉장고 담기, 찜, 최근 본 선택 삭제, 사용자 id).
여기서는 풀 자리표시자를 둔 앱(`validating_client`)으로 봅니다. 검증을 지나면 자리표시자에서 500 이 나와 구분됩니다.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

HUGE = 2**63
USER = {"X-User-Id": "1"}


@pytest.mark.parametrize(
    ("method", "path", "body", "params"),
    [
        ("GET", f"/api/v1/products/{HUGE}", None, None),
        ("GET", f"/api/v1/products/{HUGE}/recipes", None, None),
        ("GET", f"/api/v1/products/{HUGE}/storage-guide", None, None),
        ("GET", f"/api/v1/recipes/{HUGE}", None, None),
        ("GET", "/api/v1/recipes/1/missing-products", None, {"base_product_id": str(HUGE)}),
        ("POST", "/api/v1/users/me/fridge", {"product_id": HUGE, "quantity": 1, "unit": "개"}, None),
        ("PATCH", f"/api/v1/users/me/fridge/{HUGE}", {"quantity": 1}, None),
        ("DELETE", f"/api/v1/users/me/fridge/{HUGE}", None, None),
        ("POST", f"/api/v1/users/me/favorite-recipes/{HUGE}", None, None),
        ("POST", f"/api/v1/users/me/recent-recipes/{HUGE}", None, None),
        ("DELETE", "/api/v1/users/me/recent-recipes", {"recipe_ids": [1, HUGE]}, None),
    ],
)
async def test_ids_beyond_bigint_are_422(
    validating_client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    params: dict[str, str] | None,
) -> None:
    response = await validating_client.request(method, path, headers=USER, json=body, params=params)
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "INVALID_INPUT_VALUE"


async def test_user_id_beyond_bigint_is_401(validating_client: AsyncClient) -> None:
    """X-User-Id 가 bigint 를 넘으면 사용자 식별 실패(401)입니다. JWT 의 sub 도 같은 함수로 읽습니다."""
    response = await validating_client.get("/api/v1/users/me/fridge", headers={"X-User-Id": str(HUGE)})
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"
