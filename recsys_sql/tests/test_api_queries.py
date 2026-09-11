"""api_spec 의 읽기 엔드포인트를 받치는 쿼리 검증.

사용자 범위 쿼리는 시드 트랜잭션 안에서, 카탈로그 범위 쿼리는 실제 적재분으로 봅니다.
버블·상품처럼 전체를 대상으로 도는 쿼리는 시드 몇 건으로 규칙을 확인할 수 없습니다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import PACKAGE_DIR
from recsys_sql.fixtures import SeedIds
from recsys_sql.runner import run_query

QUERY_DIR = PACKAGE_DIR / "queries" / "openLeeWorld"

pytestmark = pytest.mark.db


def api_query(name: str) -> SqlQuery:
    """쿼리 하나를 꺼냅니다."""
    return next(query for query in load_catalog(QUERY_DIR) if query.name == name)


async def fetch(conn: AsyncConnection, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """쿼리를 돌리고 dict 목록으로 받습니다."""
    return (await run_query(conn, api_query(name), params)).as_dicts()


class TestBubbleProducts:
    """버블 -> 상품 추천."""

    async def test_products_come_from_the_bubble_own_recipes(self, db_conn: AsyncConnection) -> None:
        """추천된 상품의 재료는 그 버블 후보 레시피가 실제로 쓰는 재료여야 합니다.

        버블에서 본 상품으로 만들 수 있는 레시피가 그 버블에 없으면 흐름이 끊깁니다.
        """
        rows = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 20, "skip": 0})
        assert rows

        used = {
            row.ingredient_id
            for row in await db_conn.execute(
                text(
                    """
                    SELECT DISTINCT ri.ingredient_id
                    FROM bubble_recipe_candidate c
                    JOIN recipe_ingredient ri ON ri.recipe_id = c.recipe_id AND ri.is_required
                    WHERE c.keyword_id = 'MEAT'
                    """
                )
            )
        }
        assert {row["ingredient_id"] for row in rows} <= used

    async def test_pantry_products_do_not_fill_the_screen(self, db_conn: AsyncConnection) -> None:
        """상비재료는 어느 버블에서나 1등이 됩니다. 빼지 않으면 화면이 소금·설탕이 됩니다."""
        rows = await fetch(db_conn, "bubble_products", {"keyword_id": "LIGHT", "max_results": 30, "skip": 0})
        pantry = {
            row.ingredient_id
            for row in await db_conn.execute(text("SELECT ingredient_id FROM ingredient WHERE is_pantry"))
        }
        assert rows
        assert not {row["ingredient_id"] for row in rows} & pantry

    async def test_skip_pages_without_overlap(self, db_conn: AsyncConnection) -> None:
        """페이지가 겹치면 같은 상품이 두 번 보입니다."""
        first = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 5, "skip": 0})
        second = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 5, "skip": 5})

        assert len(first) == 5
        assert not {row["product_id"] for row in first} & {row["product_id"] for row in second}


class TestBubbleRuleIsDefinedOnce:
    """버블 규칙 해석이 한 벌인지."""

    async def test_counts_and_candidates_agree(self, db_conn: AsyncConnection) -> None:
        """세는 쿼리와 목록 쿼리가 같은 수를 내야 합니다.

        예전에는 두 파일이 규칙을 따로 들고 있어, 세기로는 통과하는데 눌렀을 때
        다른 목록이 나올 수 있었습니다. 지금은 같은 뷰를 봅니다.
        """
        for row in await fetch(db_conn, "bubble_candidate_counts", {}):
            listed = await fetch(
                db_conn,
                "bubble_recipe_candidates",
                {"keyword_id": row["keyword_id"], "max_results": 100_000},
            )
            assert len(listed) == row["candidates"], row["keyword_id"]


class TestProductQueries:
    """상품 상세와 상품 -> 레시피."""

    async def test_product_detail_returns_one_row_with_ingredients(self, db_conn: AsyncConnection) -> None:
        """상세는 구성 재료를 배열로 함께 냅니다. 왕복을 둘로 나누지 않습니다."""
        product_id = (
            await db_conn.execute(
                text("SELECT product_id FROM product_ingredient WHERE role = 'PRIMARY' ORDER BY product_id LIMIT 1")
            )
        ).scalar()

        rows = await fetch(db_conn, "product_detail", {"product_id": product_id})
        assert len(rows) == 1
        assert rows[0]["ingredients"], "구성 재료가 있는 상품인데 배열이 비었습니다"

    async def test_product_without_ingredients_still_has_detail(self, db_conn: AsyncConnection) -> None:
        """재료가 안 붙은 상품(295건)도 상세는 나와야 합니다."""
        product_id = (
            await db_conn.execute(
                text(
                    """
                    SELECT p.product_id
                    FROM product p
                    LEFT JOIN product_ingredient pi ON pi.product_id = p.product_id
                    WHERE pi.product_id IS NULL
                    ORDER BY p.product_id
                    LIMIT 1
                    """
                )
            )
        ).scalar()
        if product_id is None:
            pytest.skip("재료가 없는 상품이 적재분에 없습니다")

        rows = await fetch(db_conn, "product_detail", {"product_id": product_id})
        assert len(rows) == 1
        assert rows[0]["ingredients"] == []

    async def test_product_recipes_count_the_product_own_ingredient(self, db_conn: AsyncConnection) -> None:
        """요약의 matched_count 는 이 상품으로 채워지는 재료 수입니다."""
        product_id = (
            await db_conn.execute(
                text(
                    """
                    SELECT pi.product_id
                    FROM product_ingredient pi
                    JOIN recipe_ingredient ri ON ri.ingredient_id = pi.ingredient_id AND ri.is_required
                    WHERE pi.role = 'PRIMARY'
                    ORDER BY pi.product_id
                    LIMIT 1
                    """
                )
            )
        ).scalar()

        rows = await fetch(db_conn, "product_recipes", {"product_id": product_id, "max_results": 10})
        assert rows
        for row in rows:
            assert row["matched_count"] >= 1, "상품 자기 재료도 안 세고 있습니다"
            assert row["matched_count"] + row["missing_count"] == row["total_count"]


class TestRecipeQueries:
    """레시피 상세와 부족 재료."""

    async def test_recipe_detail_carries_lines_and_steps(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """상세 한 번에 재료줄까지 나와야 합니다."""
        rows = await fetch(db_conn, "recipe_detail", {"recipe_id": seeded.kimchi_stew})

        assert len(rows) == 1
        names = {item["name"] for item in rows[0]["ingredients"]}
        assert names == {"배추김치", "돼지고기", "두부"}
        assert rows[0]["steps"] == [], "시드에는 단계가 없습니다"

    async def test_missing_ingredients_split_by_where_they_came_from(
        self, db_conn: AsyncConnection, seeded: SeedIds
    ) -> None:
        """어디서 채워졌는지까지 구분해야 화면이 다르게 보여 줄 수 있습니다."""
        rows = await fetch(
            db_conn,
            "recipe_missing_ingredients",
            {"recipe_id": seeded.kimchi_stew, "base_product_id": seeded.pork_a, "user_id": seeded.user},
        )
        status = {row["ingredient_name"]: row["status"] for row in rows}

        assert status["돼지고기"] == "BASE", "지금 고른 상품으로 채워진 재료입니다"
        assert status["배추김치"] == "IN_FRIDGE", "냉장고에 기한이 남아 있습니다"
        assert status["두부"] == "MISSING", "냉장고의 두부는 기한이 지났습니다"

    async def test_pantry_is_not_reported_as_missing(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """소금을 사라고 하면 안 됩니다."""
        rows = await fetch(
            db_conn,
            "recipe_missing_ingredients",
            {"recipe_id": seeded.pork_grill, "base_product_id": 0, "user_id": seeded.user},
        )
        status = {row["ingredient_name"]: row["status"] for row in rows}

        assert status["소금"] == "PANTRY"

    async def test_anonymous_user_still_gets_the_list(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """로그인 전에도 이 화면은 보여야 합니다. user_id 0 은 냉장고 없음을 뜻합니다."""
        rows = await fetch(
            db_conn,
            "recipe_missing_ingredients",
            {"recipe_id": seeded.kimchi_stew, "base_product_id": 0, "user_id": 0},
        )
        status = {row["ingredient_name"]: row["status"] for row in rows}

        assert status == {"배추김치": "MISSING", "돼지고기": "MISSING", "두부": "MISSING"}


class TestMyFridge:
    """마이냉장고."""

    async def test_one_row_per_fridge_slot(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """상품 하나가 여러 줄로 갈라지면 냉장고가 이상해 보입니다."""
        rows = await fetch(db_conn, "my_fridge_items", {"user_id": seeded.user})

        assert len(rows) == 3
        assert len({row["product_id"] for row in rows}) == 3

    async def test_expired_item_is_marked_not_hidden(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """기한이 지난 것도 냅니다. 치우려면 보여야 합니다."""
        rows = await fetch(db_conn, "my_fridge_items", {"user_id": seeded.user})
        expired = [row for row in rows if row["is_expired"]]

        assert [row["product_id"] for row in expired] == [seeded.tofu_a]

    async def test_my_recipes_carry_the_missing_list(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """개수만 내면 화면이 레시피마다 재료를 다시 물어야 합니다."""
        rows = await fetch(
            db_conn,
            "my_recipe_candidates",
            {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100},
        )
        seed_rows = {row["recipe_id"]: row for row in rows if row["recipe_id"] in seeded.recipes}

        assert seeded.kimchi_stew in seed_rows
        stew = seed_rows[seeded.kimchi_stew]
        assert float(stew["match_rate"]) == 1.0
        assert stew["missing_ingredients"] == []

    async def test_missing_list_matches_missing_count(self, db_conn: AsyncConnection) -> None:
        """개수와 목록이 어긋나면 화면이 둘 중 무엇을 믿을지 알 수 없습니다."""
        rows = await fetch(
            db_conn,
            "my_recipe_candidates",
            {"user_id": 0, "min_match_rate": 0.0, "max_results": 50},
        )
        for row in rows:
            assert len(row["missing_ingredients"]) == row["missing_count"]
