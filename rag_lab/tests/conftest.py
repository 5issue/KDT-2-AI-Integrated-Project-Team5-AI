"""rag_lab 테스트 공통 픽스처."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rag_lab.config import Settings
from rag_lab.testing import FakeChatClient, FakeEmbeddingClient


def database_url_configured() -> bool:
    """DATABASE_URL 이 채워져 있는지 확인합니다. 값 자체는 노출하지 않습니다."""
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    try:
        settings = Settings()
    except Exception:
        return False
    return settings.database_url is not None and bool(settings.database_url.get_secret_value().strip())


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """DATABASE_URL 이 없으면 db 마커가 붙은 테스트를 건너뜁니다.

    이 훅은 세션 전체 item 목록을 받습니다. 이 폴더 아래 테스트만 걸러내지 않으면,
    .env 가 없는 폴더의 훅이 다른 폴더의 DB 테스트까지 skip 시켜 버립니다.
    """
    if database_url_configured():
        return
    skip = pytest.mark.skip(reason="DATABASE_URL 이 없어 건너뜁니다. rag_lab/.env 를 채우면 실행됩니다.")
    own_tests = Path(__file__).parent.resolve()
    for item in items:
        if "db" not in item.keywords:
            continue
        if own_tests in Path(str(item.path)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture
def fake_embedder() -> FakeEmbeddingClient:
    """가짜 임베딩 클라이언트."""
    return FakeEmbeddingClient()


@pytest.fixture
def fake_chat() -> FakeChatClient:
    """가짜 LLM."""
    return FakeChatClient()


@pytest.fixture
def offline_settings() -> Settings:
    """.env 를 읽지 않는 설정."""
    return Settings(_env_file=None)  # type: ignore[call-arg]
