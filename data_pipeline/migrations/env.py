"""alembic 실행 환경. DB URL 은 data_pipeline/.env 에서만 읽습니다."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from data_pipeline.config import get_settings
from data_pipeline.db import create_engine, mask_dsn

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 스키마 v0.1.0 은 이미 Neon 에 반영되어 있고 ORM 메타데이터를 정본으로 두지 않습니다.
# autogenerate 대신 명시적 마이그레이션만 씁니다.
target_metadata = None


def run_migrations_offline() -> None:
    """--sql 모드. 실제 접속 없이 SQL 만 출력합니다."""
    settings = get_settings()
    url = settings.require_database_url(direct=True)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """동기 커넥션 위에서 마이그레이션을 돌립니다."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """마이그레이션은 pooler 가 아니라 direct 엔드포인트로 붙습니다."""
    settings = get_settings()
    print(f"alembic 대상: {mask_dsn(settings.require_database_url(direct=True))}")
    engine = create_engine(direct=True, settings=settings, poolclass=None)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
