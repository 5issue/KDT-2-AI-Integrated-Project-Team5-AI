"""recsys_sql 테스트 공통 픽스처.

DB 테스트는 트랜잭션을 열고 끝나면 무조건 롤백합니다. 시드 데이터가 남지 않습니다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from recsys_fixtures import SeedIds, seed_minimal
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.config import QUERIES_DIR, Settings
from recsys_sql.db import engine_scope

TEMPLATE_DIR = QUERIES_DIR / "_template"


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
    skip = pytest.mark.skip(reason="DATABASE_URL 이 없어 건너뜁니다. recsys_sql/.env 를 채우면 실행됩니다.")
    own_tests = Path(__file__).parent.resolve()
    for item in items:
        if "db" not in item.keywords:
            continue
        if own_tests in Path(str(item.path)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture
async def db_conn() -> AsyncIterator[AsyncConnection]:
    """롤백되는 트랜잭션 위의 커넥션."""
    async with engine_scope() as engine:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                yield conn
            finally:
                await transaction.rollback()


@pytest.fixture
async def seeded(db_conn: AsyncConnection) -> SeedIds:
    """시드 데이터가 들어간 트랜잭션."""
    return await seed_minimal(db_conn)
