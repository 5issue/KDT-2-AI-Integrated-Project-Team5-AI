"""serving 테스트 공통 픽스처."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings
from serving.logging_setup import LOGGER_NAMES


@pytest.fixture(autouse=True)
def _propagate_logs_to_caplog(monkeypatch: pytest.MonkeyPatch) -> None:
    """``caplog`` 은 루트 로거에서 받습니다. 운영 로깅 설정은 중복 출력을 막으려고 서빙·rag_lab 로거의
    전파를 끄므로(logging_setup), 테스트 동안에만 다시 켭니다. 끝나면 monkeypatch 가 되돌립니다."""
    for name in LOGGER_NAMES:
        monkeypatch.setattr(logging.getLogger(name), "propagate", True)


def database_url_configured() -> bool:
    """DATABASE_URL 이 채워져 있는지 확인합니다. 값 자체는 노출하지 않습니다."""
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    try:
        settings = Settings()
    except Exception:
        return False
    return settings.database_url is not None and bool(settings.database_url.get_secret_value().strip())


RUN_LLM_OPTION = "--run-llm"


def pytest_addoption(parser: pytest.Parser) -> None:
    """실제 LLM 호출 테스트를 켜는 명시 플래그. 과금이 있어 기본은 꺼져 있습니다."""
    parser.addoption(
        RUN_LLM_OPTION,
        action="store_true",
        default=False,
        help="llm 마커 테스트(실제 OpenRouter 호출, 과금)를 돌립니다. 없으면 skip 합니다.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """DATABASE_URL 이 없으면 db 마커를, `--run-llm` 이 없으면 llm 마커를 건너뜁니다.

    이 훅은 세션 전체 item 목록을 받습니다. 이 폴더 아래 테스트만 걸러내지 않으면,
    .env 가 없는 폴더의 훅이 다른 폴더의 DB 테스트까지 skip 시켜 버립니다.

    llm 테스트는 실제 OpenRouter 를 불러 과금이 있습니다. 예전에는 `-m` 표현식에 `llm` 이라는 글자가
    있는지로 판단해, `-m "db or not llm"` 처럼 오히려 빼려는 표현식이나 `llm_mock` 같은 다른 마커 이름에도
    열렸습니다(PR #42 리뷰). 표현식을 해석하지 않고 명시 플래그로만 켭니다.
    """
    own_tests = Path(__file__).parent.resolve()
    own_items = [item for item in items if own_tests in Path(str(item.path)).resolve().parents]

    if not config.getoption(RUN_LLM_OPTION):
        skip_llm = pytest.mark.skip(reason=f"실제 LLM 호출 테스트입니다(과금). `{RUN_LLM_OPTION}` 을 주면 돕니다.")
        for item in own_items:
            if item.get_closest_marker("llm") is not None:
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
