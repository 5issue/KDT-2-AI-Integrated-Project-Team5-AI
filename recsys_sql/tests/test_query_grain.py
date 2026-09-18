"""카탈로그 쿼리의 결과 grain 검증.

같은 실수가 리뷰에서 세 번 나왔습니다 — 다대일 조인을 접지 않은 채 `LIMIT` 을 걸어
같은 대상이 여러 줄로 나오고, 그 중복이 `LIMIT` 앞에서 생겨 요청보다 적게 나가는 것.

- `bubble_products`: PRIMARY 재료가 둘인 상품
- `missing_ingredient_products`: role 을 안 봐서 SECONDARY 까지 걸림
- `product_recipes`: 한 상품이 같은 레시피의 두 재료 자리에 지정됨

쿼리마다 "한 행을 유일하게 만드는 키" 를 적어 두고 전부 한 번에 확인합니다.
새 쿼리를 추가하면 여기에도 한 줄 넣으세요.
"""

from __future__ import annotations

from typing import Any

import pytest
from recsys_fixtures import SeedIds
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import QUERIES_DIR
from recsys_sql.runner import run_query

pytestmark = pytest.mark.db

# (쿼리 이름, 결과 한 행을 유일하게 만드는 키)
GRAIN: dict[str, tuple[str, ...]] = {
    "bubble_candidate_counts": ("keyword_id",),
    "bubble_products": ("product_id",),
    "bubble_recipe_candidates": ("recipe_id",),
    "fridge_recipe_match": ("recipe_id",),
    "missing_ingredient_products": ("ingredient_id", "product_id"),
    "my_fridge_items": ("product_id",),
    "my_recipe_candidates": ("recipe_id",),
    "product_detail": ("product_id",),
    "product_recipes": ("recipe_id",),
    "product_storage_guideline": ("storage_location", "storage_context"),
    "recipe_detail": ("recipe_id",),
    "recipe_missing_ingredients": ("ingredient_id",),
    "reorder_candidates": ("product_id",),
}


def catalog() -> dict[str, SqlQuery]:
    """카탈로그 전체를 이름으로."""
    return {query.name: query for query in load_catalog(QUERIES_DIR)}


def test_every_query_declares_its_grain() -> None:
    """새 쿼리를 추가하고 여기 빠뜨리면 검사에서 빠집니다."""
    assert set(catalog()) == set(GRAIN)


async def params_for(conn: AsyncConnection, name: str, seeded: SeedIds) -> dict[str, Any]:
    """시드 사용자와 실제 적재분을 섞어 비어 있지 않은 결과를 만듭니다."""
    product_id = (
        await conn.execute(
            text(
                """
                SELECT pi.product_id
                FROM product_ingredient pi
                JOIN storage_guideline sg ON sg.ingredient_id = pi.ingredient_id
                WHERE pi.role = 'PRIMARY'
                  -- PRIMARY 가 둘 이상인 상품은 보관법 쿼리가 일부러 제외합니다.
                  -- 그런 상품을 고르면 결과가 비어 검사가 무의미해집니다.
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM product_ingredient pick
                      WHERE pick.product_id = pi.product_id
                        AND pick.role = 'PRIMARY'
                  )
                GROUP BY pi.product_id, sg.storage_location, sg.storage_context
                HAVING COUNT(*) > 1
                ORDER BY COUNT(*) DESC
                LIMIT 1
                """
            )
        )
    ).scalar() or seeded.tofu_a

    return {
        "bubble_candidate_counts": {},
        "bubble_products": {"keyword_id": "MEAT", "max_results": 200, "skip": 0},
        "bubble_recipe_candidates": {"keyword_id": "MEAT", "max_results": 500},
        "fridge_recipe_match": {"user_id": seeded.user, "min_coverage": 0.0, "max_results": 500},
        "missing_ingredient_products": {
            "user_id": seeded.user,
            "recipe_id": seeded.tofu_braise,
            "max_per_ingredient": 50,
        },
        "my_fridge_items": {"user_id": seeded.user},
        "my_recipe_candidates": {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 500},
        "product_detail": {"product_id": product_id},
        "product_recipes": {"product_id": seeded.tofu_a, "max_results": 500, "skip": 0},
        "product_storage_guideline": {"product_id": product_id},
        "recipe_detail": {"recipe_id": seeded.kimchi_stew},
        "recipe_missing_ingredients": {
            "recipe_id": seeded.kimchi_stew,
            "base_product_id": seeded.pork_a,
            "user_id": seeded.user,
        },
        "reorder_candidates": {"user_id": seeded.user, "days_since": 30, "max_results": 500},
    }[name]


@pytest.mark.parametrize("name", sorted(GRAIN))
async def test_rows_are_unique_by_their_grain(db_conn: AsyncConnection, seeded: SeedIds, name: str) -> None:
    """같은 대상이 두 줄로 나오면 화면이 중복을 보여 주고 LIMIT 칸을 낭비합니다."""
    query = catalog()[name]
    rows = (await run_query(db_conn, query, await params_for(db_conn, name, seeded))).as_dicts()

    assert rows, f"{name}: 결과가 비어 검사가 무의미합니다. 파라미터를 손봐 주세요."

    key = GRAIN[name]
    keys = [tuple(row[column] for column in key) for row in rows]
    duplicates = len(keys) - len(set(keys))

    assert duplicates == 0, f"{name}: {'+'.join(key)} 기준 중복 {duplicates}건"
