"""/api/v1 경로와 공통 envelope 적용 테스트. DB 없이 돕니다.

합의 사항:
- 비즈니스 API 는 /api/v1 아래로 옮기고 모든 응답을 envelope 로 감쌉니다.
- 헬스체크는 envelope 적용 대상에서 제외하고 status code 로만 판단합니다.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings

ENVELOPE_FIELDS = {"status", "message", "data", "error", "timestamp"}


async def test_recommendations_live_under_api_v1(offline_client: AsyncClient) -> None:
    """추천 엔드포인트는 /api/v1 아래에 있고, DB 미연결 503 도 envelope 로 답합니다."""
    response = await offline_client.get("/api/v1/users/1/recipe-recommendations")

    assert response.status_code == 503
    body = response.json()
    assert set(body.keys()) == ENVELOPE_FIELDS
    assert body["status"] == "ERROR"
    assert body["error"] == "SERVICE_UNAVAILABLE"
    assert body["data"] is None


async def test_legacy_unprefixed_paths_are_gone(offline_client: AsyncClient) -> None:
    """/api/v1 없는 옛 경로는 더 이상 라우팅되지 않습니다."""
    response = await offline_client.get("/users/1/recipe-recommendations")

    assert response.status_code == 404


async def test_validation_error_uses_envelope(validating_client: AsyncClient) -> None:
    """422 도 envelope 로 답하고, 코드는 백엔드 카탈로그의 INVALID_INPUT_VALUE 를 재사용합니다."""
    response = await validating_client.get("/api/v1/users/1/recipe-recommendations", params={"limit": "999"})

    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == ENVELOPE_FIELDS
    assert body["status"] == "ERROR"
    assert body["error"] == "INVALID_INPUT_VALUE"
    assert body["data"] is None


async def test_unknown_api_route_returns_envelope_404(offline_client: AsyncClient) -> None:
    """/api/v1 아래의 없는 경로는 RESOURCE_NOT_FOUND envelope 로 답합니다."""
    response = await offline_client.get("/api/v1/definitely-missing")

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["error"] == "RESOURCE_NOT_FOUND"


async def test_health_endpoints_stay_plain(offline_client: AsyncClient) -> None:
    """헬스체크는 envelope 를 쓰지 않습니다. 기존 응답 모양 그대로여야 합니다."""
    live = await offline_client.get("/health")
    ready = await offline_client.get("/health/db")

    assert live.status_code == 200
    assert "timestamp" not in live.json()
    assert live.json()["status"] == "ok"

    assert ready.status_code == 503
    assert "detail" in ready.json()


async def test_prefix_match_requires_path_boundary(offline_client: AsyncClient) -> None:
    """/api/v10 처럼 prefix 만 비슷한 경로는 envelope 대상이 아닙니다."""
    response = await offline_client.get("/api/v10/users/1/recipe-recommendations")

    assert response.status_code == 404
    assert "detail" in response.json()


def _crashing_app():
    """일부러 터지는 라우트를 붙인 앱. 500 변환 동작 확인용."""
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    app.state.pool = None

    @app.get("/api/v1/_boom")
    async def _api_boom() -> None:
        raise RuntimeError("postgres://user:supersecret@host/db")

    @app.get("/_boom")
    async def _plain_boom() -> None:
        raise RuntimeError("bang")

    return app


async def test_unhandled_exception_returns_envelope_500() -> None:
    """처리되지 않은 예외도 /api/v1 에서는 envelope 500 으로 답합니다."""
    transport = ASGITransport(app=_crashing_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/_boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) == ENVELOPE_FIELDS
    assert body["status"] == "ERROR"
    assert body["error"] == "INTERNAL_SERVER_ERROR"
    assert body["data"] is None


async def test_unhandled_exception_does_not_leak_details() -> None:
    """예외 메시지(자격증명 등)가 응답 본문에 새어 나가면 안 됩니다."""
    transport = ASGITransport(app=_crashing_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/_boom")

    assert "supersecret" not in response.text
    assert "RuntimeError" not in response.text


async def test_unhandled_exception_outside_api_stays_plain() -> None:
    """/api/v1 밖에서는 기본 동작(플레인 500)을 유지합니다."""
    transport = ASGITransport(app=_crashing_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_boom")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
