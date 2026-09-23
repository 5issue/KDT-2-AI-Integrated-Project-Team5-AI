"""home / products 엔드포인트 테스트. DB 없이 돕니다.

명세 v0.2 대응: 13장(버블), 15장(상품 상세), 16장(상품 레시피), 22장(보관 가이드).
인증 불필요(사용자 컨텍스트 없음). 응답 필드는 명세에 있는 것만 냅니다.
"""

from __future__ import annotations

import json

from httpx import AsyncClient

from serving.schemas import (
    BubbleItem,
    ProductDetailResponse,
    ProductRecipeItem,
    StorageGuideItem,
)


async def test_home_and_product_routes_exist(offline_client: AsyncClient) -> None:
    """4개 경로 모두 라우팅되고, DB 미연결이면 503 envelope 입니다."""
    for path in (
        "/api/v1/home/bubbles",
        "/api/v1/products/101",
        "/api/v1/products/101/recipes",
        "/api/v1/products/101/storage-guide",
    ):
        response = await offline_client.get(path)
        assert response.status_code == 503, path
        assert response.json()["error"] == "SERVICE_UNAVAILABLE", path


async def test_product_id_is_validated(validating_client: AsyncClient) -> None:
    """0 이하나 정수가 아닌 product_id 는 422 로 걸립니다."""
    for bad in ("0", "abc"):
        response = await validating_client.get(f"/api/v1/products/{bad}")
        assert response.status_code == 422, bad
        assert response.json()["error"] == "INVALID_INPUT_VALUE", bad


def test_bubble_item_maps_row_to_spec_shape() -> None:
    """keyword_id -> bubble_id, is_servable -> enabled 로 매핑하고 type 은 내지 않습니다."""
    item = BubbleItem.from_row(
        {
            "keyword_id": "MEAT",
            "label": "고기 든든하게",
            "description": "육류를 활용한 든든한 메뉴",
            "min_candidates": 10,
            "candidates": 28,
            "is_servable": True,
        }
    )

    assert item.bubble_id == "MEAT"
    assert item.enabled is True
    # type 은 DB 내부 분류라 응답에서 제거하기로 확정 (FE 미사용 확인)
    assert "type" not in item.model_dump()


def test_product_detail_maps_row_and_parses_ingredients() -> None:
    """ingredients jsonb 문자열을 파싱하고, 명세 15장에 있는 필드만 냅니다."""
    row = {
        "product_id": 101,
        "name": "한돈 앞다리살 500g",
        "price": 12900,
        "weight_g": 500,
        "unit_count": 1,
        "product_type": "RAW_MATERIAL",
        "storage_type": "냉장",
        "origin_country": "대한민국",
        "stock_quantity": 20,
        "is_active": True,
        "category_name": "정육",
        "ingredients": json.dumps([{"ingredient_id": 12, "name": "돼지고기"}]),
    }

    detail = ProductDetailResponse.from_row(row)

    assert detail.product_id == 101
    assert detail.price == 12900.0
    assert [i.name for i in detail.ingredients] == ["돼지고기"]
    assert "is_active" not in detail.model_dump()


def test_product_recipe_item_builds_ingredient_summary() -> None:
    """카운트 3종을 명세 16장의 ingredient_summary 객체로 묶습니다."""
    item = ProductRecipeItem.from_row(
        {
            "recipe_id": 1001,
            "name": "돼지고기 김치찌개",
            "image_url": None,
            "difficulty": "EASY",
            "prep_time_min": 5,
            "cook_time_min": 15,
            "servings": 2,
            "cooking_method": "끓이기",
            "total_count": 5,
            "matched_count": 1,
            "missing_count": 4,
            "total_recipes": 12,
        }
    )

    assert item.ingredient_summary.total_count == 5
    assert item.ingredient_summary.matched_count == 1
    assert item.ingredient_summary.missing_count == 4


def test_storage_guide_item_maps_row() -> None:
    """보관 가이드 한 줄 매핑. tips 는 원천(FoodKeeper)이 단일 텍스트입니다."""
    item = StorageGuideItem.from_row(
        {
            "product_id": 101,
            "product_name": "한돈 앞다리살 500g",
            "storage_type": "냉장",
            "ingredient_id": 12,
            "ingredient_name": "돼지고기",
            "storage_location": "냉장",
            "storage_context": "구매후",
            "duration_min": 3,
            "duration_max": 5,
            "duration_unit": "일",
            "duration_text": "3-5 일",
            "storage_tips": "구매 후 냉장 보관하세요.",
        }
    )

    assert item.storage_location == "냉장"
    assert item.storage_context == "구매후"
    assert item.duration_text == "3-5 일"
    assert item.tips == "구매 후 냉장 보관하세요."
