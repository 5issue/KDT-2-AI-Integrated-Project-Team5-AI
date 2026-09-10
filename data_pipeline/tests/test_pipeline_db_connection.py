"""Neon 연결 설정 및 실제 연결 테스트.

DSN 변환/마스킹은 DB 없이 항상 돌고, 실제 접속 테스트는 DATABASE_URL 이 있을 때만 돕니다.
"""

from __future__ import annotations

import pytest

from data_pipeline.db import EXPECTED_TABLES, check_connection, mask_dsn, normalize_neon_url

HOST = "ep-cool-frog-123.ap-southeast-1.aws.neon.tech"
POOLED_HOST = HOST.replace("-123.", "-123-pooler.")
POOLED = f"postgresql://alice:secret@{POOLED_HOST}/neondb?sslmode=require&channel_binding=require"
DIRECT = f"postgresql://alice:secret@{HOST}/neondb?sslmode=require"


def test_normalize_upgrades_scheme_and_strips_libpq_params() -> None:
    """asyncpg 가 모르는 sslmode/channel_binding 은 URL 에서 빠지고 ssl 인자로 옮겨집니다."""
    url, connect_args = normalize_neon_url(POOLED)

    assert url.startswith("postgresql+asyncpg://")
    assert "sslmode" not in url
    assert "channel_binding" not in url
    assert connect_args["ssl"] == "require"


def test_normalize_disables_prepared_statements_for_pooler() -> None:
    """pooler 엔드포인트는 PgBouncer transaction 모드라 prepared statement 캐시를 꺼야 합니다."""
    _, pooled_args = normalize_neon_url(POOLED)
    assert pooled_args["prepared_statement_cache_size"] == 0
    assert pooled_args["statement_cache_size"] == 0
    assert pooled_args["prepared_statement_name_func"]() != pooled_args["prepared_statement_name_func"]()


def test_normalize_keeps_prepared_statements_for_direct() -> None:
    """direct 엔드포인트에서는 prepared statement 를 그대로 씁니다."""
    _, direct_args = normalize_neon_url(DIRECT)
    assert "prepared_statement_cache_size" not in direct_args


def test_normalize_rejects_unknown_scheme() -> None:
    """psycopg 등 다른 드라이버 DSN 은 조용히 넘기지 않고 실패시킵니다."""
    with pytest.raises(ValueError, match="지원하지 않는 DSN"):
        normalize_neon_url("mysql://user:pw@host/db")


def test_mask_dsn_hides_credentials_and_host() -> None:
    """로그에 남는 문자열에 자격증명이나 호스트가 들어가면 안 됩니다."""
    masked = mask_dsn(POOLED)

    assert "alice" not in masked
    assert "secret" not in masked
    assert "neon.tech" not in masked
    assert "neondb" in masked
    assert "pooled" in masked
    assert "direct" in mask_dsn(DIRECT)


@pytest.mark.db
async def test_connects_to_neon_and_sees_schema() -> None:
    """실제 Neon 브랜치에 붙어 pgvector 와 기대 테이블을 확인합니다."""
    report = await check_connection()

    assert report.server_version.startswith("PostgreSQL")
    assert report.vector_extension is not None, "pgvector 확장이 없습니다: CREATE EXTENSION vector"
    assert not report.missing_tables, f"누락 테이블: {report.missing_tables}"
    assert len(report.present_tables) == len(EXPECTED_TABLES)


@pytest.mark.db
async def test_direct_endpoint_also_reachable() -> None:
    """마이그레이션/COPY 용 direct 엔드포인트도 붙는지 확인합니다."""
    report = await check_connection(direct=True)
    assert report.database
