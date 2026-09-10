"""Neon + pgvector 연결 테스트. DATABASE_URL 이 있을 때만 돕니다."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_lab.config import get_settings
from rag_lab.db import check_connection, engine_scope
from rag_lab.retrieval import search, to_vector_literal

pytestmark = pytest.mark.db


@pytest.fixture
async def db_conn():  # type: ignore[no-untyped-def]
    """롤백되는 트랜잭션 위의 커넥션."""
    async with engine_scope() as engine:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                yield conn
            finally:
                await transaction.rollback()


async def test_pgvector_extension_is_installed() -> None:
    """벡터 검색을 하려면 pgvector 확장이 있어야 합니다."""
    report = await check_connection()
    assert report.vector_extension is not None, "CREATE EXTENSION vector 가 필요합니다."


async def test_embedding_columns_have_expected_dimension(db_conn: AsyncConnection) -> None:
    """recipe/product/ingredient 의 embedding 차원이 설정과 맞아야 합니다."""
    settings = get_settings()
    rows = (
        await db_conn.execute(
            text(
                "SELECT c.relname AS table_name, a.atttypmod AS dimension "
                "FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE a.attname = 'embedding' AND t.typname = 'vector' "
                "  AND c.relname IN ('recipe', 'product', 'ingredient')"
            )
        )
    ).mappings()
    dimensions = {row["table_name"]: int(row["dimension"]) for row in rows}

    assert set(dimensions) == {"recipe", "product", "ingredient"}, f"embedding 컬럼 누락: {dimensions}"
    for table, dimension in dimensions.items():
        assert dimension == settings.embedding_dim, f"{table}.embedding 차원이 {dimension} 입니다."


async def test_vector_literal_round_trips(db_conn: AsyncConnection) -> None:
    """파이썬 벡터 리터럴을 Postgres 가 그대로 받아들이는지 확인합니다."""
    settings = get_settings()
    vector = [0.0] * settings.embedding_dim
    vector[0] = 1.0

    distance = (
        await db_conn.execute(
            text("SELECT CAST(:vec AS vector) <=> CAST(:vec AS vector) AS distance"),
            {"vec": to_vector_literal(vector)},
        )
    ).scalar_one()
    assert float(distance) == pytest.approx(0.0, abs=1e-6)


async def test_search_query_executes(db_conn: AsyncConnection) -> None:
    """검색 SQL 이 실제 스키마에서 문법/컬럼 오류 없이 돕니다. 결과가 0건이어도 통과입니다."""
    settings = get_settings()
    vector = [0.01] * settings.embedding_dim

    for source in ("recipe", "product", "ingredient"):
        docs = await search(db_conn, vector, source=source, threshold=-1.0, settings=settings)  # type: ignore[arg-type]
        assert isinstance(docs, list)
