"""GET /api/v1/recommendations/my-recipes 테스트. DB 없이 돕니다.

명세 v0.2 21장 대응:
- 경로는 /users/{user_id}/... 가 아니라 /recommendations/my-recipes 입니다.
- 사용자 식별은 X-User-Id 헤더로 합니다 (BFF 공유 시크릿 확정 전 임시 방식).
- 응답 item 은 recommendation_reason / match / missing_ingredients 를 포함합니다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from rag_lab.reason_service import ReasonResult
from serving.app import create_app
from serving.config import Settings
from serving.routers import recommendations
from serving.schemas import MyRecipeItem

PATH = "/api/v1/recommendations/my-recipes"
USER_HEADER = {"X-User-Id": "1"}


def _sample_row() -> dict[str, Any]:
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
        "held_ingredients": json.dumps(
            [{"ingredient_id": i, "name": n} for i, n in ((3, "돼지고기"), (4, "두부"), (5, "대파"))]
        ),
        "pantry_ingredients": json.dumps([{"ingredient_id": 6, "name": "소금"}]),
    }


class _FakePool:
    """``acquire().fetch()`` 가 정해진 행을 돌려주는 풀. SQL 은 실행하지 않습니다."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakePool]:
        yield self

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return self.rows


@asynccontextmanager
async def _client_with_rows(
    rows: list[dict[str, Any]], *, reason_client: object | None = None, vocabulary: frozenset[str] = frozenset()
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    app.state.pool = _FakePool(rows)
    app.state.reason_client = reason_client
    app.state.ingredient_vocabulary = vocabulary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


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
    response = await validating_client.get(PATH, headers=USER_HEADER, params={"limit": "999"})
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_INPUT_VALUE"


def test_min_match_rate_is_no_longer_a_parameter() -> None:
    """min_match_rate 는 FE 합의로 닫혔습니다 (서버 고정 0.5). OpenAPI 계약에서 제거를 고정합니다."""
    from serving.app import create_app
    from serving.config import Settings

    app = create_app(Settings(_env_file=None, environment="local"))  # type: ignore[call-arg]
    operation = app.openapi()["paths"][PATH]["get"]
    parameter_names = [param["name"] for param in operation.get("parameters", [])]

    assert "min_match_rate" not in parameter_names
    assert "limit" in parameter_names


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
    """기본 추천 이유는 reason_service 의 규칙 기반 문구(템플릿 v2)입니다. 부족 재료 이름을 언급합니다."""
    item = MyRecipeItem.from_row(_sample_row())

    assert "김치" in item.recommendation_reason
    assert item.recommendation_reason.endswith("만들 수 있어요.")

    full = _sample_row()
    full.update(available_count=5, missing_count=0, missing_ingredients="[]")
    complete = MyRecipeItem.from_row(full)
    assert "바로 만들어 보세요" in complete.recommendation_reason


async def test_my_recipes_without_llm_client_uses_template_reason() -> None:
    """클라이언트가 없으면(키 미설정) reason_service 가 전부 규칙 문구를 돌려줍니다."""
    async with _client_with_rows([_sample_row()]) as client:
        response = await client.get(PATH, headers=USER_HEADER)

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["recommendation_reason"] == MyRecipeItem.from_row(_sample_row()).recommendation_reason


async def test_my_recipes_injects_generated_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    """라우터는 SQL 행 전체와 앱 상태의 클라이언트·사전을 서비스에 넘기고, 결과 문구를 카드에 덮어씁니다."""
    calls: list[dict[str, Any]] = []
    sentinel_client = object()
    vocabulary = frozenset({"김치", "돼지고기"})

    async def fake_generate(rows: list[dict[str, Any]], client: object, **kwargs: Any) -> list[ReasonResult]:
        calls.append({"rows": rows, "client": client, **kwargs})
        return [ReasonResult(f"LLM 문구 {index}", "llm") for index in range(len(rows))]

    monkeypatch.setattr(recommendations, "generate_reasons_for_rows", fake_generate)
    rows = [_sample_row(), {**_sample_row(), "recipe_id": 1002, "name": "두부조림"}]
    async with _client_with_rows(rows, reason_client=sentinel_client, vocabulary=vocabulary) as client:
        response = await client.get(PATH, headers=USER_HEADER)

    assert response.status_code == 200
    reasons = [item["recommendation_reason"] for item in response.json()["data"]["items"]]
    assert reasons == ["LLM 문구 0", "LLM 문구 1"]
    assert len(calls) == 1
    assert calls[0]["client"] is sentinel_client
    assert calls[0]["vocabulary"] == vocabulary
    assert [row["recipe_id"] for row in calls[0]["rows"]] == [1001, 1002]
    assert "held_ingredients" in calls[0]["rows"][0]


async def test_lifespan_without_openrouter_key_starts_and_stops_cleanly() -> None:
    """키가 없으면 클라이언트 없이 뜨고, 종료 때 AttributeError 없이 내려갑니다 (팀장 리뷰 반영)."""
    app = create_app(Settings(_env_file=None, shutdown_delay_seconds=0))  # type: ignore[call-arg]
    async with app.router.lifespan_context(app):
        assert app.state.reason_http is None
        assert app.state.reason_client is None
        assert app.state.ingredient_vocabulary == frozenset()


async def test_lifespan_with_openrouter_key_creates_and_closes_one_http_client() -> None:
    """키가 있으면 httpx 클라이언트 하나를 만들고 종료 때 닫습니다. DB 가 없으면 사전은 비어 있습니다."""
    settings = Settings(_env_file=None, shutdown_delay_seconds=0, openrouter_api_key="test-key")  # type: ignore[call-arg]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        http = app.state.reason_http
        assert isinstance(http, httpx.AsyncClient)
        assert app.state.reason_client is not None
        assert app.state.ingredient_vocabulary == frozenset()
    assert http.is_closed
    assert app.state.reason_http is None
