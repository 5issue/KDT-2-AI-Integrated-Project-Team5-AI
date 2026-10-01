"""Prometheus `/metrics` 테스트. DB 없이 돕니다.

앱마다 레지스트리가 따로라 테스트끼리 섞이지 않지만, `/metrics` 를 읽는 요청 순서와 무관하게 보려고 값은
요청 전후 차이로 봅니다.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings

USER = {"X-User-Id": "1"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """DB 없이 뜬 앱. API 경로는 503 이라 상태 코드별 집계를 볼 수 있습니다."""
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    app.state.pool = None

    @app.get("/api/v1/_metrics_boom")
    async def _boom() -> None:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _count(client: AsyncClient, handler: str, status: str) -> float:
    """`http_requests_total` 에서 handler·status 가 맞는 GET 표본의 값. 없으면 0."""
    body = (await client.get("/metrics")).text
    pattern = re.compile(
        r'^http_requests_total\{handler="' + re.escape(handler) + r'",method="GET",status="' + status + r'"\} (\S+)$',
        re.MULTILINE,
    )
    match = pattern.search(body)
    return float(match.group(1)) if match else 0.0


async def test_requests_are_counted_by_route_template(client: AsyncClient) -> None:
    """id 마다가 아니라 경로 템플릿 하나로 셉니다. 상태 코드는 묶지 않아 503 이 그대로 보입니다."""
    handler = "/api/v1/products/{product_id}"
    before = await _count(client, handler, "503")
    await client.get("/api/v1/products/12", headers=USER)
    await client.get("/api/v1/products/34", headers=USER)
    assert await _count(client, handler, "503") == before + 2


async def test_unhandled_exception_is_counted_as_500(client: AsyncClient) -> None:
    """처리되지 않은 예외도 500 으로 셉니다. FE 가 본 500 을 지표로 먼저 확인할 수 있습니다."""
    handler = "/api/v1/_metrics_boom"
    before = await _count(client, handler, "500")
    response = await client.get(handler, headers=USER)
    assert response.status_code == 500
    assert await _count(client, handler, "500") == before + 1


async def test_probes_scrapes_and_unmatched_paths_are_not_counted(client: AsyncClient) -> None:
    """헬스체크, `/metrics` 자신, 라우트에 맞지 않는 경로는 시계열을 만들지 않습니다."""
    await client.get("/health")
    await client.get("/api/v1/does-not-exist-12345", headers=USER)
    body = (await client.get("/metrics")).text
    assert 'handler="/health"' not in body
    # 앱마다 레지스트리를 따로 두어도 런타임 지표는 남깁니다. process_* 는 리눅스(/proc)에서만 나옵니다.
    assert "python_info" in body and "python_gc_objects_collected_total" in body
    assert 'handler="/metrics"' not in body
    assert "does-not-exist-12345" not in body


async def test_metrics_is_hidden_from_docs_and_access_log(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """`/metrics` 는 API 문서에 나오지 않고, 주기적인 스크레이프가 액세스 로그를 덮지 않습니다."""
    with caplog.at_level("INFO", logger="serving.access"):
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert not [r for r in caplog.records if r.name == "serving.access"]
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/metrics" not in paths
