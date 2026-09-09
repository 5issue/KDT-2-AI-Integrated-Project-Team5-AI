"""Alembic 환경 설정.

접속 문자열은 저장소에 두지 않는다. `.env` 에서 읽으며, 찾는 순서는 아래와 같다.

    1. 이미 설정된 프로세스 환경변수
    2. 저장소 루트의 `.env`
    3. `data_pipeline/.env`

이 저장소는 루트 `.env` 를 두지 않고 폴더별 `.env` 만 쓴다(루트 `.env.example` 참고).
그래서 루트만 보면 아무 데서도 값을 찾지 못한다. 3번이 그 경우를 받는다.

DDL 은 pooler 가 아니라 direct 엔드포인트로 거는 편이 안전하므로
`DATABASE_URL_DIRECT` 가 있으면 그쪽을 먼저 쓴다. 없으면 `DATABASE_URL` 을 쓴다.

production 브랜치에 실수로 적용하지 않도록, 엔드포인트를 확인하고 싶으면
`ALEMBIC_ALLOWED_HOST_PREFIX` 환경변수에 접두사를 지정한다.
"""

import os
import urllib.parse
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

REPO_ROOT = Path(__file__).resolve().parents[2]

# 먼저 읽은 쪽이 이긴다. load_dotenv 는 이미 있는 값을 덮어쓰지 않는다.
for candidate in (REPO_ROOT / ".env", REPO_ROOT / "data_pipeline" / ".env"):
    if candidate.exists():
        load_dotenv(candidate)

database_url = os.getenv("DATABASE_URL_DIRECT") or os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL 이 없다. 저장소 루트의 .env 또는 data_pipeline/.env 를 확인한다. 환경변수로 직접 넘겨도 된다."
    )

allowed_prefix = os.getenv("ALEMBIC_ALLOWED_HOST_PREFIX")
if allowed_prefix:
    host = urllib.parse.urlparse(database_url).hostname or ""
    if not host.startswith(allowed_prefix):
        raise RuntimeError(f"허용되지 않은 대상이다: {host} (기대 접두사 {allowed_prefix})")

# 이 저장소는 psycopg 3 을 쓴다. SQLAlchemy 기본값인 psycopg2 로 가지 않도록 드라이버를 지정한다.
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)

config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# 이 저장소는 ORM 모델을 두지 않는다. 마이그레이션은 손으로 쓴다.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
