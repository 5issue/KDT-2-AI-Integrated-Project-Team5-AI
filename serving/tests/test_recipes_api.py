"""recipes 엔드포인트 테스트. DB 없이 돕니다.

명세 v0.2 대응: 17장(레시피 상세), 18장(부족 재료 계산).
missing-ingredients 는 비로그인도 허용됩니다 — X-User-Id 가 없으면 냉장고 갈래를
쓰지 않고, 있는데 형식이 틀리면 401 입니다.
"""

from __future__ import annotations

import json

from httpx import AsyncClient

from serving.schemas import MissingIngredientsResponse, RecipeDetailResponse


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


def _missing_rows() -> list[dict]:
    common = {"recipe_id": 1001, "quantity": None, "unit": None}
    return [
        {**common, "ingredient_id": 12, "ingredient_name": "돼지고기", "is_required": True, "status": "BASE"},
        {**common, "ingredient_id": 2, "ingredient_name": "김치", "is_required": True, "status": "MISSING"},
        {**common, "ingredient_id": 3, "ingredient_name": "두부", "is_required": True, "status": "IN_FRIDGE"},
        {**common, "ingredient_id": 7, "ingredient_name": "소금", "is_required": True, "status": "PANTRY"},
        {**common, "ingredient_id": 9, "ingredient_name": "청양고추", "is_required": False, "status": "MISSING"},
    ]


async def test_recipe_routes_exist(offline_client: AsyncClient) -> None:
    """두 경로 모두 라우팅되고, DB 미연결이면 503 envelope 입니다."""
    for path in ("/api/v1/recipes/1001", "/api/v1/recipes/1001/missing-ingredients"):
        response = await offline_client.get(path)
        assert response.status_code == 503, path
        assert response.json()["error"] == "SERVICE_UNAVAILABLE", path


async def test_recipe_id_is_validated(validating_client: AsyncClient) -> None:
    """0 이하나 정수가 아닌 recipe_id 는 422 입니다."""
    for bad in ("0", "abc"):
        response = await validating_client.get(f"/api/v1/recipes/{bad}")
        assert response.status_code == 422, bad


async def test_missing_ingredients_allows_anonymous(offline_client: AsyncClient) -> None:
    """X-User-Id 없이도 접근 가능합니다 (503 은 DB 미연결 때문이지 401 이 아님)."""
    response = await offline_client.get("/api/v1/recipes/1001/missing-ingredients")

    assert response.status_code == 503


async def test_missing_ingredients_rejects_malformed_user_header(
    validating_client: AsyncClient,
) -> None:
    """헤더가 있는데 형식이 틀리면 조용히 무시하지 않고 401 입니다."""
    response = await validating_client.get("/api/v1/recipes/1001/missing-ingredients", headers={"X-User-Id": "abc"})

    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


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


def test_missing_ingredients_builds_summary_and_missing_items() -> None:
    """필수 재료 기준으로 집계하고, missing_items 는 MISSING 상태의 필수 재료만 냅니다."""
    resp = MissingIngredientsResponse.from_rows(recipe_id=1001, base_product=None, rows=_missing_rows())

    assert resp.ingredients.total == 4
    assert resp.ingredients.missing == 1
    assert resp.ingredients.available == 3
    assert [m.name for m in resp.missing_items] == ["김치"]
    assert resp.missing_items[0].status == "MISSING"
