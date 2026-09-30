"""찜한 레시피 / 최근 본 레시피 엔드포인트 테스트. DB 없이 돕니다.

My 레시피 화면 하단 두 줄 대응. 모든 엔드포인트는 X-User-Id 필수이고, 찜은 (user, recipe)
한 쌍이 한 행, 조회 기록은 다시 보면 시각만 갱신됩니다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from httpx import AsyncClient

from serving.schemas import FavoriteRecipeItem, RecentRecipeItem

FAVORITES = "/api/v1/users/me/favorite-recipes"
RECENTS = "/api/v1/users/me/recent-recipes"
USER = {"X-User-Id": "1"}

DELETE_BODY = {"recipe_ids": [1001, 1002]}

# (메서드, 경로, json body). 선택 삭제만 body 가 있습니다.
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", FAVORITES, None),
    ("POST", f"{FAVORITES}/1001", None),
    ("DELETE", f"{FAVORITES}/1001", None),
    ("GET", RECENTS, None),
    ("POST", f"{RECENTS}/1001", None),
    ("DELETE", RECENTS, DELETE_BODY),
]


def _card_row() -> dict[str, Any]:
    """카탈로그 두 쿼리가 공통으로 내는 카드 컬럼."""
    return {
        "recipe_id": 1001,
        "name": "닭가슴살 샐러드",
        "image_url": "https://example.com/r.jpg",
        "difficulty": "EASY",
        "cook_time_min": 10,
        "servings": 1,
    }


async def test_all_routes_require_user(validating_client: AsyncClient) -> None:
    """X-User-Id 없으면 전부 401 UNAUTHORIZED envelope 입니다."""
    for method, path, body in ROUTES:
        response = await validating_client.request(method, path, json=body)
        assert response.status_code == 401, (method, path)
        assert response.json()["error"] == "UNAUTHORIZED", (method, path)


async def test_routes_exist(offline_client: AsyncClient) -> None:
    """헤더가 유효하면 라우팅되고, DB 미연결이면 503 envelope 입니다."""
    for method, path, body in ROUTES:
        response = await offline_client.request(method, path, headers=USER, json=body)
        assert response.status_code == 503, (method, path)
        assert response.json()["error"] == "SERVICE_UNAVAILABLE", (method, path)


async def test_recipe_id_and_limit_are_validated(validating_client: AsyncClient) -> None:
    """recipe_id 0 이하, limit 범위 밖은 422 INVALID_INPUT_VALUE 입니다."""
    bad = [
        ("POST", f"{FAVORITES}/0", None),
        ("DELETE", f"{FAVORITES}/abc", None),
        ("POST", f"{RECENTS}/-1", None),
        ("GET", FAVORITES, {"limit": 0}),
        ("GET", FAVORITES, {"limit": 101}),
        ("GET", RECENTS, {"limit": 51}),
    ]
    for method, path, params in bad:
        response = await validating_client.request(method, path, headers=USER, params=params)
        assert response.status_code == 422, (method, path, params)
        assert response.json()["error"] == "INVALID_INPUT_VALUE", (method, path, params)


async def test_bulk_delete_body_is_validated(validating_client: AsyncClient) -> None:
    """recipe_ids 는 1~100 건, 각 id 는 1 이상이어야 합니다. body 가 없어도 422 입니다."""
    bad: list[dict[str, Any] | None] = [
        None,
        {},
        {"recipe_ids": []},
        {"recipe_ids": [0]},
        {"recipe_ids": ["abc"]},
        {"recipe_ids": list(range(1, 102))},
    ]
    for body in bad:
        response = await validating_client.request("DELETE", RECENTS, headers=USER, json=body)
        assert response.status_code == 422, body
        assert response.json()["error"] == "INVALID_INPUT_VALUE", body


def test_favorite_item_maps_row_to_card() -> None:
    """user_favorite_recipes 행을 카드 + favorited_at 으로 바꿉니다."""
    at = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    item = FavoriteRecipeItem.from_row({**_card_row(), "favorited_at": at})

    assert item.recipe_id == 1001
    assert item.name == "닭가슴살 샐러드"
    assert item.favorited_at == at
    assert set(item.model_dump()) == {
        "recipe_id",
        "name",
        "image_url",
        "difficulty",
        "cook_time_min",
        "servings",
        "favorited_at",
    }


def test_recent_item_maps_row_to_card() -> None:
    """user_recent_recipes 행을 카드 + viewed_at 으로 바꿉니다. image_url 은 없을 수 있습니다."""
    at = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    item = RecentRecipeItem.from_row({**_card_row(), "image_url": None, "viewed_at": at})

    assert item.image_url is None
    assert item.viewed_at == at
    assert "favorited_at" not in item.model_dump()
