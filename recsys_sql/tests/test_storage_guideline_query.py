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


async def test_one_row_per_location_and_context(db_conn: AsyncConnection) -> None:
    """같은 (장소, 상황)이 여러 번 나오면 화면이 같은 칸을 반복해서 찍습니다.

    FoodKeeper 가 한 재료를 여러 갈래로 나눠 둬서(햄 하나에 19줄) 그대로 내보내면
    `냉장 · 구매후` 만 열아홉 번 나옵니다.
    """
    product_id = await scalar(
        db_conn,
        """
        SELECT pi.product_id
        FROM product_ingredient pi
        JOIN storage_guideline sg ON sg.ingredient_id = pi.ingredient_id
        WHERE pi.role = 'PRIMARY'
        GROUP BY pi.product_id, sg.storage_location, sg.storage_context
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """,
    )
    assert product_id is not None, "중복이 있는 상품이 적재분에 없습니다"

    rows = await fetch(db_conn, product_id)
    slots = [(row["storage_location"], row["storage_context"]) for row in rows]

    assert len(slots) == len(set(slots))


async def test_conflicting_durations_return_the_shortest(db_conn: AsyncConnection) -> None:
    """기간이 어긋나면 짧은 쪽을 냅니다.

    `게류 냉장 구매후` 에 `10-12개월` 과 `2-4 일` 이 함께 있습니다. 긴 쪽을 보여 주면
    상한 음식을 먹으라고 하는 셈입니다. 고를 수 없으면 짧은 쪽이 안전합니다.
    """
    row = (
        await db_conn.execute(
            text(
                """
                SELECT pi.product_id, sg.storage_location, sg.storage_context
                FROM product_ingredient pi
                JOIN storage_guideline sg ON sg.ingredient_id = pi.ingredient_id
                JOIN product p ON p.product_id = pi.product_id
                WHERE pi.role = 'PRIMARY'
                  AND (p.storage_type IS NULL OR sg.storage_location = p.storage_type)
                GROUP BY pi.product_id, sg.storage_location, sg.storage_context
                HAVING COUNT(DISTINCT (sg.duration_min, sg.duration_max, sg.duration_unit)) > 1
                ORDER BY pi.product_id
                LIMIT 1
                """
            )
        )
    ).first()
    if row is None:
        pytest.skip("기간이 어긋나는 중복이 적재분에 없습니다")

    days = """
        COALESCE(duration_max, duration_min) * CASE duration_unit
            WHEN '시간' THEN 1.0 / 24 WHEN '일' THEN 1 WHEN '주' THEN 7
            WHEN '개월' THEN 30 WHEN '년' THEN 365 ELSE 1 END
    """
    shortest = await scalar(
        db_conn,
        f"""
        SELECT duration_text
        FROM storage_guideline sg
        JOIN product_ingredient pi ON pi.ingredient_id = sg.ingredient_id
        WHERE pi.product_id = {row.product_id}
          AND pi.role = 'PRIMARY'
          AND sg.storage_location = '{row.storage_location}'
          AND sg.storage_context = '{row.storage_context}'
        ORDER BY {days} ASC NULLS LAST, (sg.storage_tips IS NOT NULL) DESC, sg.storage_id
        LIMIT 1
        """,
    )

    rows = await fetch(db_conn, row.product_id)
    chosen = [
        item
        for item in rows
        if (item["storage_location"], item["storage_context"]) == (row.storage_location, row.storage_context)
    ]

    assert len(chosen) == 1
    assert chosen[0]["duration_text"] == shortest
