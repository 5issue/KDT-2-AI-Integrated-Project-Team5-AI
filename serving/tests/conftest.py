"""serving 테스트 공통 픽스처."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings


def database_url_configured() -> bool:
    """DATABASE_URL 이 채워져 있는지 확인합니다. 값 자체는 노출하지 않습니다."""
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    try:
        settings = Settings()
    except Exception:
        return False
    return settings.database_url is not None and bool(settings.database_url.get_secret_value().strip())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """DATABASE_URL 이 없으면 db 마커를, `-m` 에 llm 이 없으면 llm 마커를 건너뜁니다.

    이 훅은 세션 전체 item 목록을 받습니다. 이 폴더 아래 테스트만 걸러내지 않으면,
    .env 가 없는 폴더의 훅이 다른 폴더의 DB 테스트까지 skip 시켜 버립니다.

    llm 테스트는 실제 OpenRouter 를 불러 과금이 있습니다. `-m db` 로 DB 테스트를 돌릴 때
    딸려 가지 않게, `-m llm` 처럼 직접 지목했을 때만 돕니다.
    """
    own_tests = Path(__file__).parent.resolve()
    own_items = [item for item in items if own_tests in Path(str(item.path)).resolve().parents]

    if "llm" not in (config.getoption("markexpr", default="") or ""):
        skip_llm = pytest.mark.skip(reason="실제 LLM 호출 테스트입니다. `-m llm` 으로 지목하면 돕니다.")
        for item in own_items:
            if "llm" in item.keywords:
                item.add_marker(skip_llm)

    if database_url_configured():
        return
    skip = pytest.mark.skip(reason="DATABASE_URL 이 없어 건너뜁니다. serving/.env 를 채우면 실행됩니다.")
    for item in own_items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
async def offline_client() -> AsyncIterator[AsyncClient]:
    """DB 없이 뜬 앱에 붙는 클라이언트. lifespan 을 타지 않아 풀이 만들어지지 않습니다."""
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    app.state.pool = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def validating_client() -> AsyncIterator[AsyncClient]:
    """입력 검증만 보기 위한 클라이언트.

    풀 자리에 자리표시자를 넣어 의존성 단계를 통과시킵니다. FastAPI 는 의존성을 먼저 풀기
    때문에, 풀이 없으면 잘못된 입력이라도 422 가 아니라 503 이 먼저 나옵니다.
    검증이 실패하면 엔드포인트 본문이 실행되지 않으므로 이 자리표시자는 쓰이지 않습니다.
    """
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    app.state.pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def live_client() -> AsyncIterator[AsyncClient]:
    """실제 DB 풀까지 띄운 앱에 붙는 클라이언트. lifespan 을 통과시킵니다."""
    # 종료 유예(ALB 대기)는 테스트에서 끕니다. 켜 두면 테스트마다 5초씩 기다립니다.
    app = create_app(Settings(shutdown_delay_seconds=0))
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
