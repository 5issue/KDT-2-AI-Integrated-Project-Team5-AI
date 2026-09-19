"""recipes 엔드포인트 테스트. DB 없이 돕니다.

명세 v0.2 대응: 17장(레시피 상세), 18장(부족 재료 계산).
missing-ingredients 는 비로그인도 허용됩니다 — X-User-Id 가 없으면 냉장고 갈래를
쓰지 않고, 있는데 형식이 틀리면 401 입니다.
"""

from __future__ import annotations

import json

from httpx import AsyncClient

from serving.schemas import MissingProductsResponse, RecipeDetailResponse


def _detail_row() -> dict:
    return {
        "recipe_id": 1001,
        "name": "돼지고기 김치찌개",
        "description": "얼큰한 국물 요리",
        "image_url": "https://example.com/r.jpg",
        "difficulty": "EASY",
        "prep_time_min": 5,
        "cook_time_min": 15,
        "servings": 2,
        "cooking_method": "끓이기",
        "cuisine_type": "한식",
        "nutrition": json.dumps({"protein_g": 32, "sodium_mg": 900}),
        "tags": None,
        "category_name": "찌개",
        "ingredients": json.dumps(
            [
                {
                    "ingredient_id": 12,
                    "name": "돼지고기",
                    "quantity": 300,
                    "unit": "g",
                    "is_required": True,
                    "is_pantry": False,
                    "purpose": None,
                }
            ]
        ),
        "steps": json.dumps([{"step_no": 1, "instruction": "고기를 볶는다", "image_url": None}]),
    }


def _missing_product_rows() -> list[dict]:
    """missing_products 쿼리가 내는 행 모양 (재료 x 상품 랭킹)."""
    return [
        {
            "ingredient_id": 2,
            "ingredient_name": "김치",
            "product_id": 55,
            "product_name": "포기김치 1kg",
            "price": 8900,
            "recommendation_priority": 1,
            "popularity_score": 0.7,
            "rank_in_ingredient": 1,
        },
        {
            "ingredient_id": 2,
            "ingredient_name": "김치",
            "product_id": 56,
            "product_name": "맛김치 500g",
            "price": 5900,
            "recommendation_priority": 0,
            "popularity_score": 0.3,
            "rank_in_ingredient": 2,
        },
        {
            "ingredient_id": 3,
            "ingredient_name": "두부",
            "product_id": 71,
            "product_name": "국산콩 두부 300g",
            "price": 2500,
            "recommendation_priority": 0,
            "popularity_score": 0.5,
            "rank_in_ingredient": 1,
        },
    ]


async def test_recipe_routes_exist(offline_client: AsyncClient) -> None:
    """두 경로 모두 라우팅되고, DB 미연결이면 503 envelope 입니다."""
    for path in ("/api/v1/recipes/1001", "/api/v1/recipes/1001/missing-products"):
        response = await offline_client.get(path)
        assert response.status_code == 503, path
        assert response.json()["error"] == "SERVICE_UNAVAILABLE", path


async def test_recipe_id_is_validated(validating_client: AsyncClient) -> None:
    """0 이하나 정수가 아닌 recipe_id 는 422 입니다."""
    for bad in ("0", "abc"):
        response = await validating_client.get(f"/api/v1/recipes/{bad}")
        assert response.status_code == 422, bad


async def test_missing_ingredients_path_is_gone(offline_client: AsyncClient) -> None:
    """폐기된 18장 경로는 라우팅되지 않습니다 (missing-products 로 통일)."""
    response = await offline_client.get("/api/v1/recipes/1001/missing-ingredients")

    assert response.status_code == 404


async def test_missing_products_allows_anonymous(offline_client: AsyncClient) -> None:
    """X-User-Id 없이도 접근 가능합니다 (503 은 DB 미연결 때문이지 401 이 아님)."""
    response = await offline_client.get("/api/v1/recipes/1001/missing-products")

    assert response.status_code == 503


async def test_missing_products_rejects_malformed_user_header(
    validating_client: AsyncClient,
) -> None:
    """헤더가 있는데 형식이 틀리면 조용히 무시하지 않고 401 입니다."""
    response = await validating_client.get("/api/v1/recipes/1001/missing-products", headers={"X-User-Id": "abc"})

    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


async def test_missing_products_validates_params(validating_client: AsyncClient) -> None:
    """base_product_id 음수, max_per_ingredient 범위 밖은 422 입니다."""
    for params in ({"base_product_id": "-1"}, {"max_per_ingredient": "0"}, {"max_per_ingredient": "99"}):
        response = await validating_client.get("/api/v1/recipes/1001/missing-products", params=params)
        assert response.status_code == 422, params


def test_missing_products_groups_rows_by_ingredient() -> None:
    """재료별로 상품을 묶어 명세 모양으로 냅니다. 정렬은 rank 순입니다."""
    resp = MissingProductsResponse.from_rows(recipe_id=1001, rows=_missing_product_rows())

    assert resp.recipe_id == 1001
    assert [m.name for m in resp.missing_ingredients] == ["김치", "두부"]
    kimchi = resp.missing_ingredients[0]
    assert [p.rank for p in kimchi.products] == [1, 2]
    assert kimchi.products[0].price == 8900.0


def test_recipe_detail_maps_row_and_parses_jsonb() -> None:
    """ingredients/steps/nutrition jsonb 를 파싱해 명세 17장 모양으로 냅니다."""
    detail = RecipeDetailResponse.from_row(_detail_row())

    assert detail.recipe_id == 1001
    assert detail.nutrition == {"protein_g": 32, "sodium_mg": 900}
    assert [i.name for i in detail.ingredients] == ["돼지고기"]
    assert detail.steps[0].step_no == 1
    dumped = detail.model_dump()
    assert "cuisine_type" not in dumped
    assert "category_name" not in dumped
