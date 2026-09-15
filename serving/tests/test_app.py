"""앱 라우팅/응답 테스트. DB 없이 돕니다."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from serving import __version__
from serving.app import create_app
from serving.config import Settings
from serving.queries import ALLOWED_QUERIES, load_sql


async def test_health_does_not_touch_db(offline_client: AsyncClient) -> None:
    """liveness 는 DB 없이도 200 이어야 합니다."""
    response = await offline_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


async def test_db_health_returns_503_without_pool(offline_client: AsyncClient) -> None:
    """풀이 없으면 예외로 터지지 않고 503 으로 답합니다."""
    response = await offline_client.get("/health/db")

    assert response.status_code == 503
    assert "데이터베이스" in response.json()["detail"]


async def test_recommendation_endpoint_returns_503_without_pool(offline_client: AsyncClient) -> None:
    """DB 가 필요한 엔드포인트도 마찬가지입니다."""
    response = await offline_client.get("/api/v1/users/1/recipe-recommendations")

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/users/1/recipe-recommendations", {"min_coverage": "1.5"}),
        ("/api/v1/users/1/recipe-recommendations", {"limit": "999"}),
        ("/api/v1/users/0/reorder-candidates", {}),
        ("/api/v1/users/1/reorder-candidates", {"days_since": "0"}),
    ],
)
async def test_query_parameters_are_validated(
    validating_client: AsyncClient, path: str, params: dict[str, str]
) -> None:
    """범위를 벗어난 입력은 DB 까지 가지 않고 422 로 걸립니다."""
    response = await validating_client.get(path, params=params)

    assert response.status_code == 422


def test_docs_are_closed_outside_local() -> None:
    """local 이 아니면 문서 페이지와 openapi.json 을 닫습니다."""
    local = create_app(Settings(_env_file=None, environment="local"))  # type: ignore[call-arg]
    prod = create_app(Settings(_env_file=None, environment="prod"))  # type: ignore[call-arg]

    assert local.docs_url == "/docs"
    assert prod.docs_url is None
    assert prod.openapi_url is None


def test_only_whitelisted_sql_is_loadable() -> None:
    """화이트리스트 밖의 파일명은 로드되지 않습니다."""
    for name in ALLOWED_QUERIES:
        assert load_sql(name).strip()

    with pytest.raises(KeyError, match="허용되지 않은"):
        load_sql("../../etc/passwd")


def test_promoted_sql_uses_positional_params() -> None:
    """serving 의 SQL 은 asyncpg 위치 파라미터를 써야 합니다."""
    for name in ALLOWED_QUERIES:
        sql = load_sql(name)
        assert "$1" in sql, name
        # `::type` 캐스팅이 아닌 `:name` 형태의 이름 바인딩이 남아 있으면 asyncpg 가 못 읽습니다.
        body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        assert " :" not in body, f"{name}: 이름 바인딩이 남아 있습니다."
