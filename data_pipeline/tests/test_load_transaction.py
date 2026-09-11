"""실제 Neon 에서 적재 SQL 을 검증합니다. 전부 롤백되므로 DB 에 남지 않습니다.

여기서 잡으려는 것은 세 가지입니다.
- 적재 전체가 한 트랜잭션인가 (중간 실패 시 부분 적재가 남지 않는가)
- 적재 SQL 이 ingredient 마스터에 절대 쓰지 않는가 (매칭 전용 정책)
- storage_guideline 의 CHECK/UNIQUE 제약을 통과하는가

SQLAlchemy 의 asyncpg 어댑터는 SQLAlchemy 를 거친 첫 실행 전까지 트랜잭션을 시작하지
않습니다. engine.begin() 만 열고 raw 커넥션으로 내려가면 전부 autocommit 이 되어,
뒤 단계에서 실패해도 앞 단계가 남습니다. 실제로 한 번 밟았던 버그라 테스트로 고정합니다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from data_pipeline.config import get_settings
from data_pipeline.load.bulk_insert import (
    SQL_STEPS,
    StagingRows,
    assert_prerequisites,
    copy_staging,
    load_connection_scope,
    run_sql_file,
)

pytestmark = pytest.mark.db

SOURCE_TYPE = "PYTEST_ROLLBACK"


class RollbackError(Exception):
    """테스트가 끝났으니 되돌리라는 신호."""


def synthetic_rows(ingredient_id: int) -> StagingRows:
    """실제 마스터 id 하나에 붙는 최소 적재 데이터."""
    return StagingRows(
        matches=[("테스트재료", ingredient_id, "테스트재료", "exact", 1.0)],
        recipes=[
            (
                SOURCE_TYPE,
                "recipe-1",
                "롤백 테스트 레시피",
                "설명",
                "한식",
                "EASY",
                None,
                20,
                Decimal("2.00"),
                "끓이기",
                '{"calories_kcal": 100}',
                ["한식"],
                "https://example.test/recipe-1.jpg",
            )
        ],
        recipe_steps=[
            (SOURCE_TYPE, "recipe-1", 1, "재료를 손질한다", None),
            (SOURCE_TYPE, "recipe-1", 2, None, "https://example.test/step-2.jpg"),
        ],
        recipe_ingredients=[
            (
                SOURCE_TYPE,
                "recipe-1",
                1,
                "테스트재료 1개",
                "테스트재료",
                "테스트재료",
                Decimal("1.000"),
                "개",
                True,
                None,
            )
        ],
        storage=[
            (
                "pytest_item_1",
                "Test Food",
                None,
                "테스트재료",
                "dop_refrigerate",
                "냉장",
                "구매후",
                Decimal("1.00"),
                Decimal("2.00"),
                "개월",
                "구매 후 1-2개월",
                "서늘하게",
            )
        ],
    )


async def test_load_scope_opens_a_transaction() -> None:
    """적재 스코프 안에서는 반드시 트랜잭션이 열려 있어야 합니다."""
    async with load_connection_scope() as conn:
        assert conn.is_in_transaction(), "autocommit 상태입니다. 중간 실패 시 부분 적재가 남습니다."


async def test_load_scope_rolls_back_on_error() -> None:
    """스코프 안에서 예외가 나면 그 안의 쓰기가 전부 사라져야 합니다."""
    marker = "PYTEST_TX_ROLLBACK"

    with pytest.raises(RollbackError):
        async with load_connection_scope() as conn:
            await conn.execute(
                "INSERT INTO recipe (name, nutrition, tags, source_type, source_recipe_id) "
                "VALUES ($1, '{}'::jsonb, '{}'::text[], $2, $3)",
                "트랜잭션 테스트",
                marker,
                "tx-1",
            )
            assert await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", marker) == 1
            raise RollbackError

    async with load_connection_scope() as conn:
        leftover = await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", marker)
        assert leftover == 0, f"롤백되지 않고 {leftover}행이 남았습니다."


async def test_full_load_sql_runs_against_live_schema() -> None:
    """001~004 를 실제 스키마에서 돌리고 되돌립니다. 컬럼/제약 불일치를 잡습니다."""
    settings = get_settings()

    with pytest.raises(RollbackError):
        async with load_connection_scope(settings) as conn:
            ingredient_id = await conn.fetchval("SELECT ingredient_id FROM ingredient ORDER BY ingredient_id LIMIT 1")
            assert ingredient_id is not None, "ingredient 마스터가 비어 있습니다."
            before = await conn.fetchval("SELECT COUNT(*) FROM ingredient")

            await run_sql_file(conn, settings.sql_dir / SQL_STEPS[0])
            await assert_prerequisites(conn)
            await conn.execute(
                "TRUNCATE staging_recipe, staging_recipe_step, staging_recipe_ingredient, "
                "staging_storage_guideline, staging_ingredient_match"
            )
            await copy_staging(conn, synthetic_rows(int(ingredient_id)), chunk_size=100)
            for name in SQL_STEPS[1:]:
                await run_sql_file(conn, settings.sql_dir / name)

            recipes = await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", SOURCE_TYPE)
            lines = await conn.fetchval(
                "SELECT COUNT(*) FROM recipe_ingredient ri JOIN recipe r USING (recipe_id) WHERE r.source_type = $1",
                SOURCE_TYPE,
            )
            guidelines = await conn.fetchval(
                "SELECT COUNT(*) FROM storage_guideline WHERE source_item_id = 'pytest_item_1'"
            )
            steps = await conn.fetchval(
                "SELECT COUNT(*) FROM recipe_step rs JOIN recipe r USING (recipe_id) WHERE r.source_type = $1",
                SOURCE_TYPE,
            )
            assert (recipes, lines, guidelines, steps) == (1, 1, 1, 2)

            # 대표 사진과 단계 사진이 실제로 들어갔는지.
            assert (
                await conn.fetchval("SELECT image_url FROM recipe WHERE source_type = $1", SOURCE_TYPE)
                == "https://example.test/recipe-1.jpg"
            )
            # 설명 없이 사진만 있는 단계도 CHECK 를 통과해야 합니다.
            assert (
                await conn.fetchval(
                    "SELECT rs.image_url FROM recipe_step rs JOIN recipe r ON r.recipe_id = rs.recipe_id "
                    "WHERE r.source_type = $1 AND rs.step_no = 2",
                    SOURCE_TYPE,
                )
                == "https://example.test/step-2.jpg"
            )

            # 매칭 전용 정책: 적재 SQL 은 마스터에 행을 추가하지 않는다.
            assert await conn.fetchval("SELECT COUNT(*) FROM ingredient") == before

            # 같은 데이터를 다시 돌려도 늘지 않아야 한다(멱등성).
            for name in SQL_STEPS[1:]:
                await run_sql_file(conn, settings.sql_dir / name)
            again = await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", SOURCE_TYPE)
            again_guidelines = await conn.fetchval(
                "SELECT COUNT(*) FROM storage_guideline WHERE source_item_id = 'pytest_item_1'"
            )
            again_steps = await conn.fetchval(
                "SELECT COUNT(*) FROM recipe_step rs JOIN recipe r USING (recipe_id) WHERE r.source_type = $1",
                SOURCE_TYPE,
            )
            assert (again, again_guidelines, again_steps) == (1, 1, 2)

            raise RollbackError

    async with load_connection_scope() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM recipe WHERE source_type = $1", SOURCE_TYPE) == 0
        assert await conn.fetchval("SELECT COUNT(*) FROM storage_guideline WHERE source_item_id = 'pytest_item_1'") == 0
