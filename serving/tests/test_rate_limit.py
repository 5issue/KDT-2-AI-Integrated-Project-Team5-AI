"""rate limit 테스트. DB 없이 돕니다.

정책: /api/v1 아래에만 적용. 추천 엔드포인트는 더 낮은 한도. 식별 키는
X-User-Id 가 있으면 사용자, 없으면 클라이언트 IP. 초과 시 429 envelope +
Retry-After 헤더. 헬스체크는 제외.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings


@pytest.fixture
async def limited_client() -> AsyncIterator[AsyncClient]:
    """아주 낮은 한도로 앱을 띄운 클라이언트. 풀 자리표시자로 검증 단계까지 통과."""
    settings = Settings(_env_file=None, rate_limit_per_minute=3, rate_limit_reco_per_minute=2)  # type: ignore[call-arg]
    app = create_app(settings)
    app.state.pool = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_reco_endpoint_hits_lower_limit(limited_client: AsyncClient) -> None:
    """추천 한도(2회) 초과 시 429 TOO_MANY_REQUESTS envelope 로 답합니다."""
    path = "/api/v1/recommendations/my-recipes"
    headers = {"X-User-Id": "1"}
    for _ in range(2):
        response = await limited_client.get(path, headers=headers)
        assert response.status_code == 503  # DB 미연결이지만 한도는 통과

    blocked = await limited_client.get(path, headers=headers)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["status"] == "ERROR"
    assert body["error"] == "TOO_MANY_REQUESTS"
    assert "Retry-After" in blocked.headers


async def test_users_are_counted_separately(limited_client: AsyncClient) -> None:
    """사용자별로 따로 셉니다. 한 사용자가 막혀도 다른 사용자는 통과합니다."""
    path = "/api/v1/recommendations/my-recipes"
    for _ in range(3):
        await limited_client.get(path, headers={"X-User-Id": "7"})

    other = await limited_client.get(path, headers={"X-User-Id": "8"})
    assert other.status_code != 429


async def test_health_is_exempt(limited_client: AsyncClient) -> None:
    """헬스체크는 rate limit 대상이 아닙니다."""
    for _ in range(10):
        response = await limited_client.get("/health")
        assert response.status_code == 200


async def test_zero_limit_disables(offline_client: AsyncClient) -> None:
    """기본 conftest 앱(한도 미설정)은 기존 동작 그대로입니다."""
    for _ in range(5):
        response = await offline_client.get("/api/v1/home/bubbles")
        assert response.status_code == 503


def test_stale_keys_are_swept() -> None:
    """윈도우가 지난 키는 dict 에서 제거됩니다 (일회성 봇/IP 메모리 누수 방지)."""
    from serving.ratelimit import WINDOW_SECONDS, SlidingWindowLimiter

    limiter = SlidingWindowLimiter(5)
    limiter.try_acquire("one-shot-bot", now=0.0)
    assert "one-shot-bot" in limiter.tracked_keys()

    # 다른 키의 요청이 윈도우 하나를 넘긴 뒤 들어오면 청소가 돌아야 합니다.
    limiter.try_acquire("active-user", now=WINDOW_SECONDS + 1.0)

    assert "one-shot-bot" not in limiter.tracked_keys()
    assert "active-user" in limiter.tracked_keys()


async def test_reco_prefix_requires_path_boundary(limited_client: AsyncClient) -> None:
    """/api/v1/recommendations-extra 같은 유사 경로는 추천 한도(2)가 아닌 기본 한도(3)를 씁니다."""
    path = "/api/v1/recommendations-extra"
    headers = {"X-User-Id": "5"}
    responses = [await limited_client.get(path, headers=headers) for _ in range(4)]

    # 존재하지 않는 경로라 404 지만, 한도는 3회까지 통과하고 4회째 429 여야 합니다.
    assert [r.status_code for r in responses] == [404, 404, 404, 429]


async def test_zero_limits_disable_completely() -> None:
    """한도 0 이면 rate limit 이 완전히 비활성화됩니다."""
    settings = Settings(_env_file=None, rate_limit_per_minute=0, rate_limit_reco_per_minute=0)  # type: ignore[call-arg]
    app = create_app(settings)
    app.state.pool = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            response = await client.get("/api/v1/home/bubbles")
            assert response.status_code == 503


def test_negative_limits_are_rejected() -> None:
    """음수 한도는 설정 단계에서 거부됩니다 (0 만 비활성)."""
    with pytest.raises(Exception):
        Settings(_env_file=None, rate_limit_per_minute=-1)  # type: ignore[call-arg]
