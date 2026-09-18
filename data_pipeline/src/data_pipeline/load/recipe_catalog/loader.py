"""COOKRCP01 적재. **여기만 DB 에 닿습니다.**

`load-recipes` 명령 본체(`run_load_recipes`)와 그것이 부르는 COPY/INSERT 단계
(`run_recipe_load`)가 있습니다. 앞은 raw 를 찾고 집계를 내는 흐름이고, 뒤는 한
트랜잭션 안에서 staging 에 밀어넣고 타깃 테이블에 반영하는 부분입니다.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.config import Settings, get_settings
from data_pipeline.load.bulk_insert import (
    STAGING_MATCH_COLUMNS,
    STAGING_RECIPE_COLUMNS,
    STAGING_RECIPE_INGREDIENT_COLUMNS,
    STAGING_RECIPE_STEP_COLUMNS,
    LoadReport,
    load_connection_scope,
    run_sql_file,
)
from data_pipeline.load.recipe_catalog.rows import (
    REQUIRED_COLUMNS,
    RecipeRows,
    build_recipe_rows,
    write_records,
)


async def run_recipe_load(rows: RecipeRows, *, settings: Settings | None = None) -> LoadReport:
    """staging 에 COPY 하고 recipe / recipe_ingredient / recipe_step 에 반영합니다.

    `run_load` 와 같은 SQL(`002`/`003`/`005`)을 씁니다. 다른 점은 입력이 2·3단계
    산출물이 아니라 구조화된 parquet 이라는 것뿐입니다. `004`(보관기준)는 이 데이터에
    해당 내용이 없어 건너뜁니다.

    한 트랜잭션입니다. `003` 에서 실패하면 `002` 가 넣은 레시피도 남지 않습니다.
    """
    settings = settings or get_settings()
    report = LoadReport(skipped_recipes=rows.skipped)

    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / "001_staging_tables.sql")
        report.applied_sql.append("001_staging_tables.sql")

        # 레시피 계열만 비웁니다. 보관기준 staging 은 다른 작업이 쓰고 있을 수 있습니다.
        await conn.execute(
            "TRUNCATE staging_recipe, staging_recipe_step, staging_recipe_ingredient, staging_ingredient_match"
        )

        plan = (
            ("staging_recipe", STAGING_RECIPE_COLUMNS, rows.recipes),
            ("staging_recipe_step", STAGING_RECIPE_STEP_COLUMNS, rows.steps),
            ("staging_recipe_ingredient", STAGING_RECIPE_INGREDIENT_COLUMNS, rows.ingredients),
            ("staging_ingredient_match", STAGING_MATCH_COLUMNS, rows.matches),
        )
        for table, columns, records in plan:
            for start in range(0, len(records), settings.copy_chunk_size):
                await conn.copy_records_to_table(
                    table,
                    records=records[start : start + settings.copy_chunk_size],
                    columns=list(columns),
                )
            report.staged[table] = len(records)

        for name in ("002_insert_recipe.sql", "003_insert_recipe_ingredient.sql", "005_insert_recipe_step.sql"):
            await run_sql_file(conn, settings.sql_dir / name)
            report.applied_sql.append(name)

        for table in ("recipe", "recipe_ingredient", "recipe_step"):
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            report.row_counts[table] = int(count or 0)

    return report


async def run_load_recipes(
    settings: Settings, *, apply: bool, load_lookup: Callable[[], Awaitable[dict[str, int]]]
) -> int:
    """`load-recipes` 명령 본체. 구조화된 한국어 레시피(COOKRCP01)를 적재합니다.

    `load_lookup` 은 `매칭키 -> ingredient_id` 를 주는 것입니다. 마스터 조회와
    3단계 결과 병합이 `stages.resolve` 에 있어서, 적재 계층이 단계 계층을
    끌어오지 않도록 **부르는 쪽이 넣어 줍니다.**

    **재료명 산출물을 먼저 쓰고 나서 부릅니다.** `write_records` 는 3단계가 읽을
    입력이라 DB 와 무관하게 남아야 하고, 데이터셋이 없으면 그 전에 빠져나갑니다.
    """
    datasets = [
        dataset for dataset in discover_datasets(settings.raw_dir) if set(REQUIRED_COLUMNS) <= set(dataset.columns)
    ]
    if not datasets:
        print("COOKRCP01 데이터셋을 찾지 못했습니다.", file=sys.stderr)
        return 1

    # 3단계가 읽을 재료명을 남깁니다. `resolve` 를 돌리기 전에 이 파일이 있어야 합니다.
    records_path = write_records(datasets, settings)

    lookup = await load_lookup()

    rows = build_recipe_rows(datasets, lookup)
    print(f"재료명 산출물: {records_path.name}")
    print(rows.render())
    if rows.is_empty():
        return 1
    if not apply:
        print("\n실제로 적재하려면 --apply 를 붙이세요.", file=sys.stderr)
        return 0

    report = await run_recipe_load(rows, settings=settings)
    print()
    print(report.render())
    return 0
