"""적재 트랜잭션 회귀 테스트.

SQLAlchemy 의 asyncpg 어댑터는 SQLAlchemy 를 거친 첫 실행 전까지 트랜잭션을 시작하지 않습니다.
`engine.begin()` 만 열고 raw 커넥션으로 바로 내려가면 전부 autocommit 이 되어, 004 에서
실패해도 003 이 넣은 레시피가 남습니다. 실제로 한 번 밟았던 버그라 테스트로 고정합니다.
"""

from __future__ import annotations

import pytest

from data_pipeline.config import get_settings
from data_pipeline.load.bulk_insert import load_connection_scope, run_sql_file

pytestmark = pytest.mark.db


async def test_load_scope_opens_a_transaction() -> None:
    """적재 스코프 안에서는 반드시 트랜잭션이 열려 있어야 합니다."""
    async with load_connection_scope() as conn:
        assert conn.is_in_transaction(), "autocommit 상태입니다. 중간 실패 시 부분 적재가 남습니다."


async def test_load_scope_rolls_back_on_error() -> None:
    """스코프 안에서 예외가 나면 그 안의 쓰기가 전부 사라져야 합니다."""
    marker = "TEST_TX_ROLLBACK"

    with pytest.raises(RuntimeError, match="의도적 실패"):
        async with load_connection_scope() as conn:
            await conn.execute(
                "INSERT INTO recipe (name, nutrition, tags, source_type, source_recipe_id) "
                "VALUES ($1, '{}'::jsonb, '{}'::text[], $2, $3)",
                "트랜잭션 테스트 레시피",
                marker,
                "tx-1",
            )
            assert await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", marker) == 1
            raise RuntimeError("의도적 실패")

    async with load_connection_scope() as conn:
        leftover = await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", marker)
        assert leftover == 0, f"롤백되지 않고 {leftover}행이 남았습니다."


async def test_ingredient_master_is_never_written_by_matching() -> None:
    """재료는 매칭 전용입니다. 002 가 ingredient 마스터에 쓰지 않는지 확인합니다.

    임시 테이블로 staging 을 대신합니다(temp 스키마가 search_path 앞에 있어 002 가 이걸 봅니다).
    실제 테이블에는 아무것도 쓰지 않으므로 커밋되어도 남는 것이 없습니다.
    """
    async with load_connection_scope() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM ingredient")

        await conn.execute(
            "CREATE TEMP TABLE staging_recipe_ingredient (source_id text, line_no int, raw_text text, "
            "name text, normalized_name text, quantity numeric, unit text, is_required boolean, "
            "role text, purpose text) ON COMMIT DROP"
        )
        await conn.execute(
            "CREATE TEMP TABLE staging_ingredient_match (normalized_name text primary key, "
            "ingredient_id bigint, matched_name text, match_type text) ON COMMIT DROP"
        )
        await conn.execute(
            "CREATE TEMP TABLE staging_unmatched_ingredient (normalized_name text primary key, "
            "sample_raw_text text, sample_name text, occurrence_count int, recipe_count int) ON COMMIT DROP"
        )
        await conn.execute(
            "INSERT INTO staging_recipe_ingredient VALUES "
            "('r1', 1, '마늘 1큰술', '마늘', '마늘', 1, '큰술', true, 'SEASONING', null), "
            "('r1', 2, '없는재료 1개', '없는재료', '없는재료', 1, '개', true, 'PRIMARY', null)"
        )

        await run_sql_file(conn, get_settings().sql_dir / "002_match_ingredient.sql")

        assert await conn.fetchval("SELECT COUNT(*) FROM ingredient") == before, "마스터에 행이 추가되었습니다."
        assert await conn.fetchval("SELECT COUNT(*) FROM staging_ingredient_match") == 1
        assert await conn.fetchval("SELECT normalized_name FROM staging_unmatched_ingredient") == "없는재료"
