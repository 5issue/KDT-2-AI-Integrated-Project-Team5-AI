"""`load-catalog` 명령 본체. **여기만 DB 에 닿습니다.**

category -> product -> product_ingredient 순으로 staging 에 COPY 하고 `006`/`007`/`008`
SQL 로 본 테이블에 반영합니다. 순서가 중요합니다 - 상품이 카테고리를 경로로 찾고,
구성 재료가 상품을 자연키로 찾습니다.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.config import Settings
from data_pipeline.load.bulk_insert import load_connection_scope, run_sql_file
from data_pipeline.load.catalog.derive import derive_product_ingredients
from data_pipeline.load.catalog.models import (
    CATEGORY_COLUMNS,
    PRODUCT_COLUMNS,
    PRODUCT_INGREDIENT_COLUMNS,
)
from data_pipeline.load.catalog.rows import build_catalog_rows, render


async def run_load_catalog(
    settings: Settings, *, apply: bool, load_lookup: Callable[[], Awaitable[dict[str, int]]]
) -> int:
    """`load-catalog` 명령 본체. 상품 카탈로그를 적재합니다. LLM 단계를 거치지 않습니다.

    `load_lookup` 은 재료 마스터를 `매칭키 -> ingredient_id` 로 읽어 오는 것입니다.
    그 조회는 3단계(`stages.resolve`)에 있는데, 이 모듈이 거기를 직접 부르면
    적재 계층이 단계 계층에 매달립니다. 그래서 **부르는 쪽이 넣어 줍니다.**
    덕분에 이 함수는 dict 하나만 있으면 DB 없이도 앞부분을 돌릴 수 있습니다.

    **호출을 늦추는 것이 중요합니다.** 데이터셋이 없으면 마스터를 읽기 전에
    빠져나가야 합니다. 안 그러면 적재할 것도 없는데 DB 부터 붙습니다.
    """
    rows = build_catalog_rows(discover_datasets(settings.raw_dir))
    if rows.is_empty():
        print("카테고리/상품 데이터셋을 찾지 못했습니다.", file=sys.stderr)
        return 1

    # raw 에 구성 재료가 없으면 상품명과 카테고리로 유추합니다. 마스터를 읽어야 해서
    # 여기서 붙입니다. 이미 들어온 구성 재료는 건드리지 않습니다.
    lookup = await load_lookup()
    derived = derive_product_ingredients(rows, lookup)
    print(render(rows))
    if derived:
        print(f"  (상품명/카테고리로 유추한 것 {derived}건)")
    if not apply:
        print("\n--apply 를 붙이면 실제로 반영합니다. 지금은 집계만 했습니다.")
        return 0

    plan = (
        ("staging_category", CATEGORY_COLUMNS, rows.categories),
        ("staging_product", PRODUCT_COLUMNS, rows.products),
        ("staging_product_ingredient", PRODUCT_INGREDIENT_COLUMNS, rows.product_ingredients),
    )
    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / "001_staging_tables.sql")
        await conn.execute("TRUNCATE staging_category, staging_product, staging_product_ingredient")
        for table, columns, records in plan:
            for start in range(0, len(records), settings.copy_chunk_size):
                await conn.copy_records_to_table(
                    table, records=records[start : start + settings.copy_chunk_size], columns=list(columns)
                )
        for name in ("006_insert_category.sql", "007_insert_product.sql", "008_insert_product_ingredient.sql"):
            await run_sql_file(conn, settings.sql_dir / name)
        counts = {
            table: int(await conn.fetchval(f"SELECT count(*) FROM {table}") or 0)
            for table in ("category", "product", "product_ingredient")
        }
    print("\n적재 후:", ", ".join(f"{k} {v}행" for k, v in counts.items()))
    return 0
