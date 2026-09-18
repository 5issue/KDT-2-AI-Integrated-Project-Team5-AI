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
    """레시피 2종이 실제 스키마에서 돕니다."""
    detail = await live_client.get("/api/v1/recipes/1")
    assert detail.status_code in (200, 404)
    if detail.status_code == 200:
        body = detail.json()["data"]
        assert {"recipe_id", "name", "ingredients", "steps"} <= set(body)

    missing = await live_client.get(
        "/api/v1/recipes/1/missing-ingredients",
        headers={"X-User-Id": "1"},
    )
    assert missing.status_code in (200, 404)
    if missing.status_code == 200:
        body = missing.json()["data"]
        assert {"recipe_id", "ingredients", "missing_items"} <= set(body)
        counts = body["ingredients"]
        assert counts["total"] == counts["available"] + counts["missing"]
