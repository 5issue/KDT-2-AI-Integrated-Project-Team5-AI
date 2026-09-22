"""앱 라우팅/응답 테스트. DB 없이 돕니다."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from serving import __version__
from serving.app import create_app
from serving.config import Settings
from serving.queries import ALLOWED_QUERIES, build_query


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


def test_docs_are_open_outside_prod_only() -> None:
    """스웨거는 local/dev 에서 열리고 prod 에서만 닫습니다.

    FE 가 연동 전 dev 데모 환경에서 브라우저로 계약을 확인할 수 있어야 합니다.
    """
    local = create_app(Settings(_env_file=None, environment="local"))  # type: ignore[call-arg]
    dev = create_app(Settings(_env_file=None, environment="dev"))  # type: ignore[call-arg]
    prod = create_app(Settings(_env_file=None, environment="prod"))  # type: ignore[call-arg]

    assert local.docs_url == "/docs"
    assert dev.docs_url == "/docs"
    assert dev.openapi_url == "/openapi.json"
    assert prod.docs_url is None
    assert prod.openapi_url is None


SAMPLES: dict[str, object] = {"int": 1, "float": 0.5, "str": "x", "bool": True}


def test_only_whitelisted_queries_are_reachable() -> None:
    """화이트리스트 밖의 이름은 만들어지지 않습니다."""
    with pytest.raises(KeyError, match="허용되지 않은"):
        build_query("../../etc/passwd", {})


def test_every_allowed_query_exists_in_the_catalog() -> None:
    """화이트리스트에 적어 둔 이름이 카탈로그에 실제로 있어야 합니다.

    복사본을 없앤 뒤로 serving 은 .sql 을 갖지 않습니다. 카탈로그에서 쿼리가 사라지거나
    이름이 바뀌면 배포 후 첫 요청에서야 알게 되므로, 여기서 미리 걸립니다.
    """
    from recsys_sql import find_query

    for name in ALLOWED_QUERIES:
        query = find_query(name)
        params = {key: SAMPLES[type_name] for key, type_name in query.params.items()}
        sql, args = build_query(name, params)

        assert len(args) == len(query.params), name
        # `::type` 캐스팅이 아닌 `:name` 형태의 이름 바인딩이 남으면 asyncpg 가 못 읽습니다.
        body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        assert " :" not in body, f"{name}: 이름 바인딩이 남아 있습니다."


def test_serving_has_no_sql_files_of_its_own() -> None:
    """복사본이 다시 생기면 언젠가 카탈로그와 갈라집니다."""
    from pathlib import Path

    package_root = Path(__file__).resolve().parents[1]
    assert not list(package_root.rglob("*.sql")), "serving 은 .sql 을 갖지 않습니다."


def test_repository_layer_does_not_drag_in_sqlalchemy() -> None:
    """서빙은 asyncpg 만 씁니다. 검증 하나 때문에 ORM 전체가 이미지에 들어가면 안 됩니다.

    `recsys_sql` 을 통째로 의존하게 되면서 생긴 위험입니다. import 경로가 늘어나면
    조용히 딸려 들어오므로 별도 프로세스에서 확인합니다.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import serving.queries, sys; print('sqlalchemy' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", "serving.queries 가 SQLAlchemy 를 끌어옵니다."
