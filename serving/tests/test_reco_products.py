"""GET /api/v1/recommendations/products (RECO-01, 명세 14장) 테스트. DB 없이 돕니다."""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient

from serving.schemas import BubbleProductsResponse

PATH = "/api/v1/recommendations/products"


def _product_rows() -> list[dict[str, Any]]:
    """bubble_products 쿼리가 내는 행 모양."""
    return [
        {
            "product_id": 101,
            "name": "한돈 앞다리살 500g",
            "price": 12900,
            "weight_g": 500,
            "product_type": "RAW_MATERIAL",
            "storage_type": "냉장",
            "origin_country": "대한민국",
            "stock_quantity": 20,
            "recipe_count": 12,
            "total_count": 40,
            "ingredients": json.dumps([{"ingredient_id": 12, "name": "돼지고기"}]),
        }
    ]


async def test_route_exists(offline_client: AsyncClient) -> None:
    """라우팅되고, DB 미연결이면 503 envelope 입니다."""
    response = await offline_client.get(PATH, params={"bubble_id": "MEAT"})

    assert response.status_code == 503
    assert response.json()["error"] == "SERVICE_UNAVAILABLE"


async def test_bubble_id_is_required(validating_client: AsyncClient) -> None:
    """bubble_id 없거나 비면 422 INVALID_INPUT_VALUE 입니다."""
    for params in ({}, {"bubble_id": ""}):
        response = await validating_client.get(PATH, params=params)
        assert response.status_code == 422, params
        assert response.json()["error"] == "INVALID_INPUT_VALUE", params


async def test_limit_is_validated(validating_client: AsyncClient) -> None:
    """limit 범위 밖은 422 입니다."""
    response = await validating_client.get(PATH, params={"bubble_id": "MEAT", "limit": "999"})

    assert response.status_code == 422


def test_response_maps_rows_to_spec_shape() -> None:
    """행을 명세 14장 모양(product + recommendation)으로 묶습니다."""
    resp = BubbleProductsResponse.from_rows(bubble_id="MEAT", bubble_label="고기 든든하게", rows=_product_rows())

    assert resp.bubble.id == "MEAT"
    assert resp.bubble.label == "고기 든든하게"
    item = resp.items[0]
    assert item.product.product_id == 101
    assert item.product.price == 12900.0
    assert item.recommendation.score == 12.0
    assert "12" in item.recommendation.reason
    assert "recipe_count" not in item.product.model_dump()
