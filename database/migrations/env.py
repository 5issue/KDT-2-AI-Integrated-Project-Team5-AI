"""Alembic 환경 설정.

접속 문자열은 저장소에 두지 않는다. 기본으로 저장소 루트의 `.env` 에 있는
`DATABASE_URL`을 읽고, `DATABASE_URL_ENV_KEY`로 다른 환경변수 키를 선택할 수 있다.
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

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# 기본값은 기존 DATABASE_URL 이다. 임시 Neon 브랜치를 검증할 때만
# DATABASE_URL_ENV_KEY=DATABASE_URL_DEV_SUBEOM2 처럼 대상 키를 명시한다.
database_url_env_key = os.getenv("DATABASE_URL_ENV_KEY", "DATABASE_URL")
database_url = os.getenv(database_url_env_key)
if not database_url:
    raise RuntimeError(f"{database_url_env_key} 이(가) 없다. 저장소 루트의 .env 를 확인한다.")

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
