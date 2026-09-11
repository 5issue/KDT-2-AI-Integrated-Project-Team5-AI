"""상품 보관법 조회 검증.

실제 적재분을 봅니다. 시드 몇 건으로는 "PRIMARY 가 둘인 상품" 같은 조건이 나오지 않아
규칙이 지켜지는지 알 수 없기 때문입니다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import PACKAGE_DIR
from recsys_sql.runner import run_query

QUERY_DIR = PACKAGE_DIR / "queries" / "openLeeWorld"

pytestmark = pytest.mark.db


def storage_query() -> SqlQuery:
    """보관법 쿼리를 꺼냅니다."""
    return next(query for query in load_catalog(QUERY_DIR) if query.name == "product_storage_guideline")


async def fetch(conn: AsyncConnection, product_id: int) -> list[dict[str, Any]]:
    """상품 하나의 보관법을 받습니다."""
    return (await run_query(conn, storage_query(), {"product_id": product_id})).as_dicts()


async def scalar(conn: AsyncConnection, sql: str) -> Any:
    """단일 값 조회."""
    return (await conn.execute(text(sql))).scalar()


async def test_single_primary_product_gets_its_ingredient_guideline(db_conn: AsyncConnection) -> None:
    """PRIMARY 재료가 하나면 그 재료의 지침이 나와야 합니다."""
    product_id = await scalar(
        db_conn,
        """
        SELECT p.product_id
        FROM product p
        JOIN product_ingredient pi ON pi.product_id = p.product_id AND pi.role = 'PRIMARY'
        JOIN storage_guideline sg ON sg.ingredient_id = pi.ingredient_id
        WHERE p.storage_type IS NOT NULL
        GROUP BY p.product_id
        HAVING COUNT(DISTINCT pi.ingredient_id) = 1
        ORDER BY p.product_id
        LIMIT 1
        """,
    )
    assert product_id is not None, "보관법이 붙는 단일 원물 상품이 하나도 없습니다"

    rows = await fetch(db_conn, product_id)
    assert rows
    assert len({row["ingredient_id"] for row in rows}) == 1


async def test_multi_primary_product_gets_nothing(db_conn: AsyncConnection) -> None:
    """PRIMARY 가 둘 이상인 상품에는 단일 보관법을 붙이지 않습니다.

    밀키트처럼 구성이 여러 개인 상품에 원물 하나의 보관 기간을 보여 주면 틀립니다.
    """
    product_id = await scalar(
        db_conn,
        """
        SELECT product_id
        FROM product_ingredient
        WHERE role = 'PRIMARY'
        GROUP BY product_id
        HAVING COUNT(*) > 1
        ORDER BY product_id
        LIMIT 1
        """,
    )
    if product_id is None:
        pytest.skip("PRIMARY 가 둘 이상인 상품이 적재분에 없습니다")

    assert await fetch(db_conn, product_id) == []


async def test_result_matches_the_product_default_location(db_conn: AsyncConnection) -> None:
    """기본 보관 장소가 있으면 그 장소의 지침만 나와야 합니다."""
    row = (
        await db_conn.execute(
            text(
                """
                SELECT p.product_id, p.storage_type
                FROM product p
                JOIN product_ingredient pi ON pi.product_id = p.product_id AND pi.role = 'PRIMARY'
                JOIN storage_guideline sg ON sg.ingredient_id = pi.ingredient_id
                WHERE p.storage_type IS NOT NULL
                GROUP BY p.product_id, p.storage_type
                HAVING COUNT(DISTINCT pi.ingredient_id) = 1
                ORDER BY p.product_id
                LIMIT 1
                """
            )
        )
    ).first()
    assert row is not None

    rows = await fetch(db_conn, row.product_id)
    assert rows
    assert {item["storage_location"] for item in rows} == {row.storage_type}


async def test_enum_columns_are_korean(db_conn: AsyncConnection) -> None:
    """화면에 그대로 나가는 값이라 한국어여야 합니다. 영어가 남아 있으면 여기서 잡습니다."""
    leftovers = (
        await db_conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM storage_guideline
                WHERE storage_location NOT IN ('냉장', '냉동', '상온')
                   OR storage_context NOT IN ('일반', '구매후', '개봉후', '해동후')
                   OR (duration_unit IS NOT NULL AND duration_unit NOT IN ('시간', '일', '주', '개월', '년'))
                """
            )
        )
    ).scalar()

    assert leftovers == 0
