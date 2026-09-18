"""GET /api/v1/recommendations/my-recipes 테스트. DB 없이 돕니다.

명세 v0.2 21장 대응:
- 경로는 /users/{user_id}/... 가 아니라 /recommendations/my-recipes 입니다.
- 사용자 식별은 X-User-Id 헤더로 합니다 (BFF 공유 시크릿 확정 전 임시 방식).
- 응답 item 은 recommendation_reason / match / missing_ingredients 를 포함합니다.
"""

from __future__ import annotations

import json

from httpx import AsyncClient

from serving.schemas import MyRecipeItem

PATH = "/api/v1/recommendations/my-recipes"
USER_HEADER = {"X-User-Id": "1"}


def _sample_row() -> dict:
    """my_recipe_candidates 쿼리가 내는 행 모양. jsonb 는 asyncpg 기본 설정에서 str 로 옵니다."""
    return {
        "recipe_id": 1001,
        "name": "돼지고기 김치찌개",
        "image_url": "https://example.com/kimchi.jpg",
        "difficulty": "EASY",
        "cook_time_min": 15,
        "servings": 2,
        "required_count": 5,
        "available_count": 4,
        "missing_count": 1,
        "match_rate": "0.800",
        "missing_ingredients": json.dumps([{"ingredient_id": 2, "name": "김치"}]),
    }


async def test_my_recipes_requires_user_header(validating_client: AsyncClient) -> None:
    """X-User-Id 가 없으면 401 UNAUTHORIZED envelope 로 답합니다."""
    response = await validating_client.get(PATH)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["error"] == "UNAUTHORIZED"
    assert body["data"] is None


async def test_my_recipes_rejects_non_numeric_user_header(validating_client: AsyncClient) -> None:
    """숫자가 아니거나 0 이하인 X-User-Id 는 401 입니다."""
    for bad in ("abc", "0", "-1"):
        response = await validating_client.get(PATH, headers={"X-User-Id": bad})
        assert response.status_code == 401, bad


async def test_my_recipes_returns_503_without_pool(offline_client: AsyncClient) -> None:
    """헤더가 유효해도 DB 미연결이면 503 envelope 입니다."""
    response = await offline_client.get(PATH, headers=USER_HEADER)

    assert response.status_code == 503
    assert response.json()["error"] == "SERVICE_UNAVAILABLE"


async def test_my_recipes_validates_query_params(validating_client: AsyncClient) -> None:
    """파라미터 범위는 DB 까지 가기 전에 422 로 걸립니다."""
    for params in ({"min_match_rate": "1.5"}, {"limit": "999"}):
        response = await validating_client.get(PATH, headers=USER_HEADER, params=params)
        assert response.status_code == 422, params
        assert response.json()["error"] == "INVALID_INPUT_VALUE"


async def test_legacy_user_scoped_paths_are_gone(offline_client: AsyncClient) -> None:
    """옛 경로와 보류된 reorder 엔드포인트는 라우팅되지 않습니다."""
    for path in (
        "/api/v1/users/1/recipe-recommendations",
        "/api/v1/users/1/reorder-candidates",
    ):
        response = await offline_client.get(path)
        assert response.status_code == 404, path


def test_my_recipe_item_maps_row_to_spec_shape() -> None:
    """SQL 행을 명세 21장 응답 모양으로 변환합니다. jsonb 문자열도 파싱합니다."""
    item = MyRecipeItem.from_row(_sample_row())

    assert item.recipe_id == 1001
    assert item.match.required_ingredients == 5
    assert item.match.available_ingredients == 4
    assert item.match.missing_ingredients == 1
    assert item.match.match_rate == 0.8
    assert [m.name for m in item.missing_ingredients] == ["김치"]


def test_recommendation_reason_mentions_missing_ingredient() -> None:
    """추천 이유는 규칙 기반 문장으로 만듭니다. 부족 재료가 있으면 이름을 언급합니다."""
    item = MyRecipeItem.from_row(_sample_row())

    assert "김치" in item.recommendation_reason

    full = _sample_row()
    full.update(available_count=5, missing_count=0, missing_ingredients="[]")
    complete = MyRecipeItem.from_row(full)
    assert "모두" in complete.recommendation_reason
