"""staging COPY + 타깃 테이블 반영. **여기만 DB 에 닿습니다.**

    1. asyncpg COPY 로 UNLOGGED staging 테이블에 밀어넣기 (네트워크 왕복 1회)
    2. `sql/` 의 INSERT ... SELECT 로 본 테이블 반영

**전체가 한 트랜잭션입니다.** 중간에 실패하면 아무것도 남지 않습니다.

주의: SQLAlchemy 의 asyncpg 어댑터는 SQLAlchemy 를 거친 첫 실행 전까지 트랜잭션을
시작하지 않습니다. `engine.begin()` 만 열고 raw 커넥션으로 내려가면 전부 autocommit 이
되므로, `load_connection_scope` 가 raw 커넥션에서 직접 트랜잭션을 엽니다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

from data_pipeline.config import Settings, get_settings
from data_pipeline.db import engine_scope
from data_pipeline.load.bulk_insert.models import (
    STAGING_MATCH_COLUMNS,
    STAGING_RECIPE_COLUMNS,
    STAGING_RECIPE_INGREDIENT_COLUMNS,
    STAGING_RECIPE_STEP_COLUMNS,
    STAGING_STORAGE_COLUMNS,
    LoadReport,
    StagingRows,
)

# ON CONFLICT 가 의존하는 유니크 인덱스. 실제 스키마에 이미 있습니다.
REQUIRED_INDEXES = (
    "recipe_source_unique_idx",
    "uq_ingredient_source_identity_key",
    "uq_storage_guideline_source_rule",
)

SQL_STEPS = (
    "001_staging_tables.sql",
    "002_insert_recipe.sql",
    "003_insert_recipe_ingredient.sql",
    "004_insert_storage_guideline.sql",
    "005_insert_recipe_step.sql",
)


@asynccontextmanager
async def load_connection_scope(settings: Settings | None = None) -> AsyncIterator[asyncpg.Connection]:
    """적재용 raw asyncpg 커넥션을 트랜잭션 안에서 내어 줍니다."""
    async with engine_scope(direct=True, settings=settings) as engine:
        async with engine.connect() as sa_conn:
            raw = await sa_conn.get_raw_connection()
            conn: asyncpg.Connection = raw.driver_connection  # type: ignore[assignment]
            async with conn.transaction():
                yield conn


async def run_sql_file(conn: asyncpg.Connection, path: Path) -> None:
    """.sql 파일을 통째로 실행합니다.

    asyncpg 는 인자가 없을 때 simple query 프로토콜을 써서 세미콜론으로 구분된
    여러 문장을 한 번에 실행할 수 있습니다.
    """
    await conn.execute(path.read_text(encoding="utf-8"))


async def assert_prerequisites(conn: asyncpg.Connection) -> None:
    """ON CONFLICT 가 기대는 유니크 인덱스가 있는지 확인합니다."""
    found = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1::text[]) "
        "UNION SELECT conname FROM pg_constraint WHERE conname = ANY($1::text[])",
        list(REQUIRED_INDEXES),
    )
    present = {row["indexname"] for row in found}
    missing = [name for name in REQUIRED_INDEXES if name not in present]
    if missing:
        raise RuntimeError(
            "필요한 유니크 제약이 없습니다: " + ", ".join(missing) + ". 다른 Neon 브랜치에 붙었는지 확인하세요."
        )


async def copy_staging(conn: asyncpg.Connection, rows: StagingRows, *, chunk_size: int) -> dict[str, int]:
    """staging 테이블에 COPY 로 적재합니다."""
    plan = (
        ("staging_ingredient_match", STAGING_MATCH_COLUMNS, rows.matches),
        ("staging_recipe", STAGING_RECIPE_COLUMNS, rows.recipes),
        ("staging_recipe_step", STAGING_RECIPE_STEP_COLUMNS, rows.recipe_steps),
        ("staging_recipe_ingredient", STAGING_RECIPE_INGREDIENT_COLUMNS, rows.recipe_ingredients),
        ("staging_storage_guideline", STAGING_STORAGE_COLUMNS, rows.storage),
    )
    staged: dict[str, int] = {}
    for table, columns, records in plan:
        for start in range(0, len(records), chunk_size):
            await conn.copy_records_to_table(table, records=records[start : start + chunk_size], columns=list(columns))
        staged[table] = len(records)
    return staged


async def run_load(
    rows: StagingRows,
    *,
    settings: Settings | None = None,
    truncate_staging_after: bool = False,
) -> LoadReport:
    """staging COPY 후 sql/ 의 문장을 순서대로 적용합니다."""
    settings = settings or get_settings()
    report = LoadReport(
        skipped_ingredients=rows.skipped_ingredients,
        skipped_storage=rows.skipped_storage,
        skipped_steps=rows.skipped_steps,
        skipped_recipes=rows.skipped_recipes,
    )

    if settings.dry_run:
        report.staged = {
            "staging_recipe": len(rows.recipes),
            "staging_recipe_step": len(rows.recipe_steps),
            "staging_recipe_ingredient": len(rows.recipe_ingredients),
            "staging_storage_guideline": len(rows.storage),
            "staging_ingredient_match": len(rows.matches),
        }
        report.applied_sql.append("(DRY_RUN: DB 쓰기 생략)")
        return report

    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / SQL_STEPS[0])
        await assert_prerequisites(conn)
        await conn.execute(
            "TRUNCATE staging_recipe, staging_recipe_step, staging_recipe_ingredient, "
            "staging_storage_guideline, staging_ingredient_match"
        )
        report.applied_sql.append(SQL_STEPS[0])

        report.staged = await copy_staging(conn, rows, chunk_size=settings.copy_chunk_size)

        for name in SQL_STEPS[1:]:
            await run_sql_file(conn, settings.sql_dir / name)
            report.applied_sql.append(name)

        for table in ("ingredient", "recipe", "recipe_ingredient", "storage_guideline"):
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            report.row_counts[table] = int(count or 0)

        if truncate_staging_after:
            await run_sql_file(conn, settings.sql_dir / "099_truncate_staging.sql")
            report.applied_sql.append("099_truncate_staging.sql")

    return report
