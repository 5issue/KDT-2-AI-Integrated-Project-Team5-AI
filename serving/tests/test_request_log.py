"""요청 id + 구조화 액세스 로그 테스트. DB 없이 돕니다.

목적: 500 이 나도 추적할 수 있게. 모든 응답에 X-Request-Id 를 싣고,
요청마다 JSON 한 줄(access log)을 남깁니다.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

REQUEST_ID_HEADER = "X-Request-Id"


async def test_every_response_carries_request_id(offline_client: AsyncClient) -> None:
    """요청 id 가 없으면 만들어서 응답 헤더로 돌려줍니다."""
    response = await offline_client.get("/api/v1/home/bubbles")

    assert REQUEST_ID_HEADER in response.headers
    assert len(response.headers[REQUEST_ID_HEADER]) >= 8


async def test_incoming_request_id_is_echoed(offline_client: AsyncClient) -> None:
    """BFF 가 보낸 유효한 요청 id 는 그대로 씁니다 (경계 간 추적)."""
    response = await offline_client.get("/api/v1/home/bubbles", headers={REQUEST_ID_HEADER: "bff-abc-12345678"})

    assert response.headers[REQUEST_ID_HEADER] == "bff-abc-12345678"


async def test_malformed_request_id_is_replaced(offline_client: AsyncClient) -> None:
    """형식이 틀린 요청 id(로그 인젝션 여지)는 버리고 새로 만듭니다."""
    response = await offline_client.get("/api/v1/home/bubbles", headers={REQUEST_ID_HEADER: "bad id!!"})

    assert response.headers[REQUEST_ID_HEADER] != "bad id!!"


async def test_access_log_is_structured_json(offline_client: AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
    """요청마다 JSON 한 줄이 남고, 추적에 필요한 필드가 들어 있습니다."""
    with caplog.at_level("INFO", logger="serving.access"):
        await offline_client.get("/api/v1/home/bubbles", headers={"X-User-Id": "1"})

    records = [r for r in caplog.records if r.name == "serving.access"]
    assert records
    entry = json.loads(records[-1].getMessage())
    assert entry["method"] == "GET"
    assert entry["path"] == "/api/v1/home/bubbles"
    assert entry["status"] == 503
    assert entry["user_id"] == "1"
    assert entry["request_id"]
    assert entry["duration_ms"] >= 0


async def test_health_is_not_logged(offline_client: AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
    """헬스체크는 액세스 로그를 남기지 않습니다 (프로브 노이즈 방지)."""
    with caplog.at_level("INFO", logger="serving.access"):
        await offline_client.get("/health")

    assert not [r for r in caplog.records if r.name == "serving.access"]
