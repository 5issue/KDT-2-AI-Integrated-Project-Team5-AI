"""missing_products_recursive(후보) 검증.

`missing_products` 는 계층 1단계만 봅니다. 후보는 같은 규칙을 재귀로 끝까지 따라갑니다.
여기서는 세 가지만 고정합니다: 계층이 1단계인 지금 데이터에서 `missing_products` 와 결과가 같다,
손자 재료에서는 조부모까지 따라가 결과가 갈린다, 순환 데이터에서도 끝난다.
"""

from __future__ import annotations

from typing import Any

import pytest
from recsys_fixtures import SeedIds, seed_child_ingredient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import QUERIES_DIR
from recsys_sql.runner import run_query

QUERY_DIR = QUERIES_DIR / "chaeyeon089"

pytestmark = pytest.mark.db


def query(name: str) -> SqlQuery:
    return next(q for q in load_catalog(QUERY_DIR) if q.name == name)


async def fetch(conn: AsyncConnection, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return (await run_query(conn, query(name), params)).as_dicts()


def missing_ingredients(rows: list[dict[str, Any]]) -> set[int]:
    return {row["ingredient_id"] for row in rows}


async def test_matches_missing_products_on_one_level_hierarchy(db_conn: AsyncConnection, seeded: SeedIds) -> None:
    """계층이 1단계면 재귀와 1단계 조회는 같은 결과를 냅니다. `missing_products` 를 1단계로 둔 근거입니다."""
    await seed_child_ingredient(
        db_conn, ingredient_id=seeded.pork + 1000, name="목심", parent_id=seeded.pork, repoint_product_id=seeded.pork_a
    )
    cases = [
        {"user_id": seeded.user, "recipe_id": seeded.kimchi_stew, "base_product_id": 0, "max_per_ingredient": 3},
        {"user_id": 0, "recipe_id": seeded.kimchi_stew, "base_product_id": seeded.pork_a, "max_per_ingredient": 3},
        {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "base_product_id": 0, "max_per_ingredient": 3},
        {"user_id": 0, "recipe_id": seeded.pork_grill, "base_product_id": 0, "max_per_ingredient": 10},
    ]

    for params in cases:
        one_level = await fetch(db_conn, "missing_products", params)
        candidate = await fetch(db_conn, "missing_products_recursive", params)
        assert candidate == one_level, params


async def test_grandchild_reaches_grandparent(db_conn: AsyncConnection, seeded: SeedIds) -> None:
    """손자 재료를 가지면 후보는 조부모까지 보유로 칩니다. `missing_products`(1단계)는 조부모를 부족으로 둡니다.

    두 쿼리가 갈리는 유일한 지점입니다. 손자 재료가 데이터에 생기면 이 차이를 보고 고릅니다.
    냉장고의 돼지고기A 를 손자 재료로 바꾸면 돼지고기 상품이 사라지므로, 부족으로 잡혔을 때
    결과에 보이도록 일반 돼지고기 상품을 하나 더 둡니다.
    """
    # 돼지고기A 의 PRIMARY 를 돼지고기 -> 목심 -> 목심 슬라이스 로 두 번 옮깁니다.
    neck = await seed_child_ingredient(
        db_conn, ingredient_id=seeded.pork + 1000, name="목심", parent_id=seeded.pork, repoint_product_id=seeded.pork_a
    )
    await seed_child_ingredient(
        db_conn, ingredient_id=seeded.pork + 2000, name="슬라이스", parent_id=neck, repoint_product_id=seeded.pork_a
    )
    plain_pork_product = seeded.pork_a + 1000
    await db_conn.execute(
        text(
            "INSERT INTO product ("
            "product_id, sku, name, product_type, price, stock_quantity, is_active, metadata, "
            "source_type, source_product_id"
            ") VALUES ("
            ":id, :sku, '돼지고기B', 'INGREDIENT', 13000, 3, TRUE, '{}'::jsonb, 'TEST-SEED', :sku"
            ")"
        ),
        {"id": plain_pork_product, "sku": "P-PORK-B"},
    )
    await db_conn.execute(
        text(
            "INSERT INTO product_ingredient (product_id, ingredient_id, role) "
            "VALUES (:product_id, :ingredient_id, 'PRIMARY')"
        ),
        {"product_id": plain_pork_product, "ingredient_id": seeded.pork},
    )
    params = {"user_id": seeded.user, "recipe_id": seeded.kimchi_stew, "base_product_id": 0, "max_per_ingredient": 3}

    one_level = await fetch(db_conn, "missing_products", params)
    candidate = await fetch(db_conn, "missing_products_recursive", params)

    assert seeded.pork in missing_ingredients(one_level)
    assert seeded.pork not in missing_ingredients(candidate)


async def test_cycle_in_hierarchy_terminates(db_conn: AsyncConnection, seeded: SeedIds) -> None:
    """A -> B -> A 순환이 있어도 CYCLE 절이 재귀를 멈춰 쿼리가 끝납니다."""
    neck = await seed_child_ingredient(
        db_conn, ingredient_id=seeded.pork + 1000, name="목심", parent_id=seeded.pork, repoint_product_id=seeded.pork_a
    )
    await db_conn.execute(
        text("UPDATE ingredient SET parent_ingredient_id = :neck WHERE ingredient_id = :pork"),
        {"neck": neck, "pork": seeded.pork},
    )
    await db_conn.execute(text("SET LOCAL statement_timeout = '5s'"))
    params = {"user_id": seeded.user, "recipe_id": seeded.kimchi_stew, "base_product_id": 0, "max_per_ingredient": 3}

    rows = await fetch(db_conn, "missing_products_recursive", params)

    assert seeded.pork not in missing_ingredients(rows)
