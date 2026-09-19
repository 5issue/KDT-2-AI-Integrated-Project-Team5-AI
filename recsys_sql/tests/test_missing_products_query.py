"""missing_products 쿼리 검증 (18장 + 30장 통일 명세용).

랭킹·품절 제외 규칙은 원본인 `missing_ingredient_products` 테스트가 이미 고정하므로,
여기서는 이 쿼리가 새로 더한 것만 봅니다: base 갈래와 비로그인(user_id=0) 동작.
"""

from __future__ import annotations

from typing import Any

import pytest
from recsys_fixtures import SeedIds
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import QUERIES_DIR
from recsys_sql.runner import run_query

QUERY_DIR = QUERIES_DIR / "chaeyeon089"

pytestmark = pytest.mark.db


def missing_products_query() -> SqlQuery:
    return next(query for query in load_catalog(QUERY_DIR) if query.name == "missing_products")


async def fetch(conn: AsyncConnection, params: dict[str, Any]) -> list[dict[str, Any]]:
    return (await run_query(conn, missing_products_query(), params)).as_dicts()


async def test_base_product_fills_its_ingredient(db_conn: AsyncConnection, seeded: SeedIds) -> None:
    """기준 상품이 채우는 재료는 부족 목록에서 빠집니다.

    시드에서 두부는 냉장고 것이 만료라 부족인데, 두부A 를 기준 상품으로 넘기면
    두부 줄이 사라져야 합니다. 방금 담은 상품을 또 추천하면 안 됩니다.
    """
    without_base = await fetch(
        db_conn,
        {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "base_product_id": 0, "max_per_ingredient": 3},
    )
    with_base = await fetch(
        db_conn,
        {
            "user_id": seeded.user,
            "recipe_id": seeded.tofu_braise,
            "base_product_id": seeded.tofu_a,
            "max_per_ingredient": 3,
        },
    )

    assert seeded.tofu in {row["ingredient_id"] for row in without_base}
    assert seeded.tofu not in {row["ingredient_id"] for row in with_base}


async def test_anonymous_user_gets_full_missing_list(db_conn: AsyncConnection, seeded: SeedIds) -> None:
    """user_id 0 (비로그인) 은 냉장고 없이 계산해, 로그인 사용자보다 부족 재료가 같거나 많습니다."""
    logged_in = await fetch(
        db_conn,
        {"user_id": seeded.user, "recipe_id": seeded.kimchi_stew, "base_product_id": 0, "max_per_ingredient": 3},
    )
    anonymous = await fetch(
        db_conn,
        {"user_id": 0, "recipe_id": seeded.kimchi_stew, "base_product_id": 0, "max_per_ingredient": 3},
    )

    logged_in_ingredients = {row["ingredient_id"] for row in logged_in}
    anonymous_ingredients = {row["ingredient_id"] for row in anonymous}

    assert logged_in_ingredients <= anonymous_ingredients
    # 냉장고에 멀쩡히 있는 김치는 로그인 사용자에게는 부족이 아니어야 합니다.
    assert seeded.kimchi not in logged_in_ingredients
    assert seeded.kimchi in anonymous_ingredients


async def test_pantry_is_never_missing_even_for_anonymous(db_conn: AsyncConnection, seeded: SeedIds) -> None:
    """상비재료(소금)는 비로그인이어도 부족으로 치지 않습니다.

    시드에서 소금은 pork_grill 에만 들어 있으므로 그 레시피를 봐야
    NOT is_pantry 분기가 실제로 검증됩니다.
    """
    anonymous = await fetch(
        db_conn,
        {"user_id": 0, "recipe_id": seeded.pork_grill, "base_product_id": 0, "max_per_ingredient": 3},
    )

    assert seeded.salt not in {row["ingredient_id"] for row in anonymous}
