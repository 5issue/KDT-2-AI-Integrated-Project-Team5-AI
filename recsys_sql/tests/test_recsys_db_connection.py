"""Neon 연결 테스트. DATABASE_URL 이 있을 때만 돕니다."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.db import EXPECTED_TABLES, check_connection


@pytest.mark.db
async def test_connects_and_sees_schema() -> None:
    """추천 쿼리가 참조하는 테이블이 전부 있는지 확인합니다."""
    report = await check_connection()

    assert report.server_version.startswith("PostgreSQL")
    assert not report.missing_tables, f"누락 테이블: {report.missing_tables}"
    assert len(report.present_tables) == len(EXPECTED_TABLES)


@pytest.mark.db
async def test_transaction_fixture_rolls_back(db_conn: AsyncConnection) -> None:
    """픽스처가 트랜잭션 안에서 돌고 있는지 확인합니다."""
    assert db_conn.in_transaction()
    value = (await db_conn.execute(text("SELECT 1"))).scalar_one()
    assert value == 1
