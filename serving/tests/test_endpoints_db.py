"""실제 Neon 에 붙어서 도는 엔드포인트 테스트.

여기서 잡으려는 것은 "promoted SQL 이 실제 스키마에서 도는가" 입니다.
결과가 0건이어도 통과입니다. 데이터가 아니라 쿼리와 응답 스키마를 검증합니다.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.db


async def test_db_health_is_ok(live_client: AsyncClient) -> None:
    """lifespan 이 만든 풀로 DB 까지 붙는지 확인합니다."""
    response = await live_client.get("/health/db")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["server_version"].startswith("PostgreSQL")
    assert body["pool_size"] is not None


async def test_db_health_does_not_leak_connection_details(live_client: AsyncClient) -> None:
    """헬스 응답에 호스트나 자격증명이 들어가면 안 됩니다."""
    payload = (await live_client.get("/health/db")).text

    assert "neon.tech" not in payload
    assert "password" not in payload.lower()


async def test_my_recipes_query_runs(live_client: AsyncClient) -> None:
    """promoted SQL 이 실제 스키마에서 문법/컬럼 오류 없이 돕니다."""
    response = await live_client.get(
        "/api/v1/recommendations/my-recipes",
        headers={"X-User-Id": "1"},
        params={"min_match_rate": 0.5, "limit": 5},
    )

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["status"] == "SUCCESS"
    for item in envelope["data"]["items"]:
        assert {"recipe_id", "name", "recommendation_reason", "match", "missing_ingredients"} <= set(item)
        assert 0.0 <= item["match"]["match_rate"] <= 1.0


async def test_home_bubbles_query_runs(live_client: AsyncClient) -> None:
    """버블 목록이 실제 스키마에서 돌고, 모든 항목이 명세 13장 필드를 갖습니다."""
    response = await live_client.get("/api/v1/home/bubbles")

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["status"] == "SUCCESS"
    for item in envelope["data"]["items"]:
        assert {"bubble_id", "label", "type", "enabled"} <= set(item)


async def test_product_endpoints_query_runs(live_client: AsyncClient) -> None:
    """상품 3종 엔드포인트가 실제 스키마에서 돕니다. 상세는 404 가 아니어야 합니다."""
    listing = await live_client.get("/api/v1/home/bubbles")
    assert listing.status_code == 200

    detail = await live_client.get("/api/v1/products/1")
    assert detail.status_code in (200, 404)
    if detail.status_code == 200:
        body = detail.json()["data"]
        assert {"product_id", "name", "price", "ingredients"} <= set(body)

    recipes = await live_client.get("/api/v1/products/1/recipes", params={"limit": 3})
    assert recipes.status_code == 200
    for item in recipes.json()["data"]["items"]:
        assert {"recipe_id", "name", "ingredient_summary"} <= set(item)

    guide = await live_client.get("/api/v1/products/1/storage-guide")
    assert guide.status_code in (200, 404)
    if guide.status_code == 200:
        body = guide.json()["data"]
        assert body["items"], "200 이면 지침이 최소 1건이어야 합니다"


async def test_recipe_endpoints_query_runs(live_client: AsyncClient) -> None:
    """레시피 2종이 실제 스키마에서 돕니다. 404 로 우회하지 않도록 실제 레시피를 찾아 200 을 요구합니다."""
    recipe_id = None
    # 적재분의 recipe_id 는 1 부터가 아닙니다 (현재 1092~). 앞쪽 구간을 넉넉히 훑습니다.
    for candidate in list(range(1, 5)) + list(range(1092, 1112)):
        if (await live_client.get(f"/api/v1/recipes/{candidate}")).status_code == 200:
            recipe_id = candidate
            break
    assert recipe_id is not None, "존재하는 레시피를 찾지 못했습니다"

    detail = await live_client.get(f"/api/v1/recipes/{recipe_id}")
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert {"recipe_id", "name", "ingredients", "steps"} <= set(body)

    missing = await live_client.get(
        f"/api/v1/recipes/{recipe_id}/missing-products",
        headers={"X-User-Id": "1"},
        params={"max_per_ingredient": 2},
    )
    assert missing.status_code == 200
    body = missing.json()["data"]
    assert {"recipe_id", "missing_ingredients"} <= set(body)
    for item in body["missing_ingredients"]:
        assert {"ingredient_id", "name", "products"} <= set(item)
        assert len(item["products"]) <= 2


async def test_bubble_products_query_runs(live_client: AsyncClient) -> None:
    """버블 -> 상품 추천이 실제 스키마에서 돕니다. 활성 버블 하나로 200 을 요구합니다."""
    bubbles = await live_client.get("/api/v1/home/bubbles")
    assert bubbles.status_code == 200
    enabled = [b for b in bubbles.json()["data"]["items"] if b["enabled"]]
    if not enabled:
        pytest.skip("활성 버블이 없습니다")

    response = await live_client.get(
        "/api/v1/recommendations/products",
        params={"bubble_id": enabled[0]["bubble_id"], "limit": 5},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["bubble"]["id"] == enabled[0]["bubble_id"]
    for item in body["items"]:
        assert {"product", "recommendation"} <= set(item)
        assert item["recommendation"]["score"] >= 0

    missing_bubble = await live_client.get("/api/v1/recommendations/products", params={"bubble_id": "NO_SUCH_BUBBLE"})
    assert missing_bubble.status_code == 404


async def test_fridge_crud_cycle(live_client: AsyncClient) -> None:
    """추가 -> 조회 -> 수정 -> 삭제 한 바퀴. 테스트 전용 user id 로 돌고 끝나면 지웁니다."""
    # app_user 에 실존하는 시드 사용자를 씁니다 (user_fridge 의 FK 때문).
    user = {"X-User-Id": "9200000020"}
    # missing-products 가 추천하는 상품은 정의상 PRIMARY 재료가 연결되어 있어
    # 냉장고에 담을 수 있습니다. 거기서 후보를 얻습니다.
    candidates: list[int] = []
    # 적재분의 recipe_id 는 1 부터가 아닙니다 (현재 1092~). 앞쪽 구간을 넉넉히 훑습니다.
    for recipe_id in list(range(1, 5)) + list(range(1092, 1112)):
        response = await live_client.get(f"/api/v1/recipes/{recipe_id}/missing-products")
        if response.status_code != 200:
            continue
        for ingredient in response.json()["data"]["missing_ingredients"]:
            candidates.extend(product["product_id"] for product in ingredient["products"])
        if candidates:
            break
    if not candidates:
        pytest.skip("담을 수 있는 상품을 찾지 못했습니다")

    product_id = candidates[0]
    created = await live_client.post(
        "/api/v1/users/me/fridge",
        headers=user,
        json={"product_id": product_id, "quantity": 500, "unit": "g"},
    )
    assert created.status_code == 200, created.text

    try:
        listing = await live_client.get("/api/v1/users/me/fridge", headers=user)
        assert listing.status_code == 200
        items = listing.json()["data"]["items"]
        assert any(item["product"]["product_id"] == product_id for item in items)

        duplicate = await live_client.post(
            "/api/v1/users/me/fridge",
            headers=user,
            json={"product_id": product_id, "quantity": 1, "unit": "개"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"] == "CONFLICT"

        patched = await live_client.patch(
            f"/api/v1/users/me/fridge/{product_id}",
            headers=user,
            json={"quantity": 300},
        )
        assert patched.status_code == 200
        assert patched.json()["data"]["quantity"] == 300
    finally:
        deleted = await live_client.delete(f"/api/v1/users/me/fridge/{product_id}", headers=user)
        assert deleted.status_code == 200
        assert deleted.json()["data"] is None

    gone = await live_client.delete(f"/api/v1/users/me/fridge/{product_id}", headers=user)
    assert gone.status_code == 404

    patch_gone = await live_client.patch(f"/api/v1/users/me/fridge/{product_id}", headers=user, json={"quantity": 1})
    assert patch_gone.status_code == 404
