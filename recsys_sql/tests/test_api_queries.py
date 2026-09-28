"""api_spec 의 읽기 엔드포인트를 받치는 쿼리 검증.

사용자 범위 쿼리는 시드 트랜잭션 안에서, 카탈로그 범위 쿼리는 실제 적재분으로 봅니다.
버블·상품처럼 전체를 대상으로 도는 쿼리는 시드 몇 건으로 규칙을 확인할 수 없습니다.
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

QUERY_DIR = QUERIES_DIR / "openLeeWorld"

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
        matched = {item["ingredient_id"] for row in rows for item in row["ingredients"]}
        assert matched <= used

    async def test_pantry_products_do_not_fill_the_screen(self, db_conn: AsyncConnection) -> None:
        """상비재료는 어느 버블에서나 1등이 됩니다. 빼지 않으면 화면이 소금·설탕이 됩니다."""
        rows = await fetch(db_conn, "bubble_products", {"keyword_id": "LIGHT", "max_results": 30, "skip": 0})
        pantry = {
            row.ingredient_id
            for row in await db_conn.execute(text("SELECT ingredient_id FROM ingredient WHERE is_pantry"))
        }
        matched = {item["ingredient_id"] for row in rows for item in row["ingredients"]}

        assert rows
        assert not matched & pantry

    async def test_skip_pages_without_overlap(self, db_conn: AsyncConnection) -> None:
        """페이지가 겹치면 같은 상품이 두 번 보입니다."""
        first = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 5, "skip": 0})
        second = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 5, "skip": 5})

        assert len(first) == 5
        assert not {row["product_id"] for row in first} & {row["product_id"] for row in second}

    async def test_each_product_appears_once(self, db_conn: AsyncConnection) -> None:
        """PRIMARY 재료가 둘인 상품이 두 줄로 나오면 중복이 LIMIT 칸을 먹습니다."""
        rows = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 50, "skip": 0})
        product_ids = [row["product_id"] for row in rows]

        assert rows
        assert len(product_ids) == len(set(product_ids))


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

        rows = await fetch(db_conn, "product_recipes", {"product_id": product_id, "max_results": 10, "skip": 0})
        assert rows
        for row in rows:
            assert row["matched_count"] >= 1, "상품 자기 재료도 안 세고 있습니다"
            assert row["matched_count"] + row["missing_count"] == row["total_count"]

    async def test_optional_only_match_is_not_a_candidate(self, db_conn: AsyncConnection) -> None:
        """선택 재료만 겹치는 레시피는 "이 상품으로 만들 수 있는 요리" 가 아닙니다.

        필수 재료를 하나도 못 채우는데 목록에 올리면 화면이 거짓말을 합니다.
        """
        product_id = (
            await db_conn.execute(
                text(
                    """
                    SELECT pi.product_id
                    FROM product_ingredient pi
                    JOIN recipe_ingredient ri ON ri.ingredient_id = pi.ingredient_id
                    WHERE pi.role = 'PRIMARY'
                    ORDER BY pi.product_id
                    LIMIT 1
                    """
                )
            )
        ).scalar()

        for row in await fetch(db_conn, "product_recipes", {"product_id": product_id, "max_results": 50, "skip": 0}):
            covered = (
                await db_conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM recipe_ingredient ri
                        JOIN product_ingredient pi ON pi.ingredient_id = ri.ingredient_id
                                                  AND pi.product_id = :product_id
                                                  AND pi.role = 'PRIMARY'
                        WHERE ri.recipe_id = :recipe_id
                          AND ri.is_required
                        """
                    ),
                    {"product_id": product_id, "recipe_id": row["recipe_id"]},
                )
            ).scalar()
            assert covered, f"{row['recipe_id']}: 필수 재료를 하나도 안 채우는데 후보가 됐습니다"


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

    async def test_child_ingredient_in_fridge_covers_parent_recipe_requirement(
        self, db_conn: AsyncConnection, seeded: SeedIds
    ) -> None:
        """목심 Product를 보유하면 돼지고기 Recipe 재료를 보유한 것으로 칩니다."""
        await seed_child_ingredient(
            db_conn,
            ingredient_id=seeded.pork + 1000,
            name="목심",
            parent_id=seeded.pork,
            repoint_product_id=seeded.pork_a,
        )

        rows = await fetch(
            db_conn,
            "my_recipe_candidates",
            {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100},
        )
        stew = next(row for row in rows if row["recipe_id"] == seeded.kimchi_stew)

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

    async def test_my_recipes_carry_held_and_pantry_lists(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """추천 이유 생성(rag_lab.reason_service)은 보유·상비 목록으로 첫 문장을 씁니다.

        상비재료는 냉장고에 있어도 pantry 쪽에만 둡니다. 두 목록이 겹치면 생성 서비스의
        불변식(보유 + 상비 = available_count)이 깨집니다.
        """
        rows = await fetch(
            db_conn,
            "my_recipe_candidates",
            {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100},
        )
        by_id = {row["recipe_id"]: row for row in rows if row["recipe_id"] in seeded.recipes}

        stew = by_id[seeded.kimchi_stew]
        assert [item["name"] for item in stew["held_ingredients"]] == ["돼지고기", "배추김치"]
        assert stew["pantry_ingredients"] == []

        grill = by_id[seeded.pork_grill]
        assert [item["name"] for item in grill["held_ingredients"]] == ["돼지고기"]
        assert [item["name"] for item in grill["pantry_ingredients"]] == ["소금"]
        assert {"ingredient_id", "name"} == set(grill["pantry_ingredients"][0])

    async def test_held_and_pantry_lists_match_available_count(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """세 목록의 길이가 count 세 개와 정확히 맞아야 생성 서비스가 행을 받아 줍니다."""
        rows = await fetch(
            db_conn,
            "my_recipe_candidates",
            {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100},
        )
        assert rows
        for row in rows:
            held = {item["name"] for item in row["held_ingredients"]}
            pantry = {item["name"] for item in row["pantry_ingredients"]}
            missing = {item["name"] for item in row["missing_ingredients"]}
            assert len(held) + len(pantry) == row["available_count"]
            assert len(missing) == row["missing_count"]
            assert not (held & pantry) and not (held & missing) and not (pantry & missing)


class TestRecipeProductPriority:
    """레시피 지정 상품 우선순위."""

    async def test_two_designated_slots_do_not_duplicate_the_recipe(
        self, db_conn: AsyncConnection, seeded: SeedIds
    ) -> None:
        """`recipe_product` 키에 ingredient_id 가 있어 한 상품이 두 자리에 지정될 수 있습니다.

        그대로 조인하면 같은 레시피가 두 번 나오고, 그 중복이 LIMIT 앞에서 생겨
        요청보다 적은 레시피가 나갑니다.
        """
        await db_conn.execute(
            text(
                "INSERT INTO recipe_product (recipe_id, ingredient_id, product_id, recommendation_priority) "
                "VALUES (:recipe_id, :ingredient_id, :product_id, 9)"
            ),
            {
                "recipe_id": seeded.tofu_braise,
                "ingredient_id": seeded.sesame_oil,
                "product_id": seeded.tofu_a,
            },
        )

        rows = await fetch(db_conn, "product_recipes", {"product_id": seeded.tofu_a, "max_results": 50, "skip": 0})
        recipe_ids = [row["recipe_id"] for row in rows]

        assert seeded.tofu_braise in recipe_ids
        assert len(recipe_ids) == len(set(recipe_ids)), "같은 레시피가 여러 번 나왔습니다"


class TestApiSpecContract:
    """api_spec 응답에 필요한 값이 쿼리에서 나오는지.

    화면이 쓰는 값이 빠지면 서빙이 왕복을 한 번 더 돌거나 값을 지어내게 됩니다.
    """

    async def test_bubble_list_carries_label_and_description(self, db_conn: AsyncConnection) -> None:
        """api_spec 13절이 label 과 description 을 함께 요구합니다."""
        rows = await fetch(db_conn, "bubble_candidate_counts", {})

        assert rows
        assert all(row["label"] for row in rows)
        assert all(row["description"] for row in rows), "버블 설명이 비어 있습니다"

    async def test_bubble_products_tell_whether_more_pages_exist(self, db_conn: AsyncConnection) -> None:
        """api_spec 14절의 has_next 를 만들 수 있어야 합니다.

        전체 개수를 함께 주지 않으면 서빙이 개수를 세는 왕복을 한 번 더 돕니다.
        """
        first = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 3, "skip": 0})

        assert len(first) == 3
        total = first[0]["total_count"]
        assert total > 3, "이 버블에는 다음 페이지가 있어야 합니다"

        tail = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 3, "skip": total - 1})
        assert len(tail) == 1, "마지막 페이지는 total_count 로 계산됩니다"

    async def test_product_recipes_are_pageable(self, db_conn: AsyncConnection) -> None:
        """api_spec 16절도 next_cursor / has_next 를 요구합니다."""
        product_id = (
            await db_conn.execute(
                text(
                    """
                    SELECT pi.product_id
                    FROM product_ingredient pi
                    JOIN recipe_ingredient ri ON ri.ingredient_id = pi.ingredient_id AND ri.is_required
                    WHERE pi.role = 'PRIMARY'
                    GROUP BY pi.product_id
                    HAVING COUNT(DISTINCT ri.recipe_id) > 3
                    ORDER BY pi.product_id
                    LIMIT 1
                    """
                )
            )
        ).scalar()
        assert product_id is not None

        first = await fetch(db_conn, "product_recipes", {"product_id": product_id, "max_results": 2, "skip": 0})
        second = await fetch(db_conn, "product_recipes", {"product_id": product_id, "max_results": 2, "skip": 2})

        assert len(first) == 2
        assert first[0]["total_recipes"] > 2
        assert not {row["recipe_id"] for row in first} & {row["recipe_id"] for row in second}

    async def test_recipe_detail_steps_are_ordered(self, db_conn: AsyncConnection) -> None:
        """조리 단계가 뒤섞여 나가면 요리가 안 됩니다."""
        recipe_id = (
            await db_conn.execute(
                text(
                    "SELECT recipe_id FROM recipe_step GROUP BY recipe_id "
                    "HAVING COUNT(*) > 3 ORDER BY recipe_id LIMIT 1"
                )
            )
        ).scalar()
        assert recipe_id is not None

        rows = await fetch(db_conn, "recipe_detail", {"recipe_id": recipe_id})
        steps = [item["step_no"] for item in rows[0]["steps"]]

        assert steps == sorted(steps)
        assert all(item["instruction"] for item in rows[0]["steps"])

    async def test_unknown_ids_return_nothing_not_an_error(self, db_conn: AsyncConnection) -> None:
        """없는 id 는 빈 결과여야 합니다. 서빙이 404 를 만들 수 있게."""
        assert await fetch(db_conn, "product_detail", {"product_id": -1}) == []
        assert await fetch(db_conn, "recipe_detail", {"recipe_id": -1}) == []

    async def test_bubble_products_rank_by_recipe_demand(self, db_conn: AsyncConnection) -> None:
        """인기도 데이터가 없어 쓰임새로 정렬합니다. 그 규칙이 실제로 지켜지는지."""
        rows = await fetch(db_conn, "bubble_products", {"keyword_id": "MEAT", "max_results": 30, "skip": 0})
        counts = [row["recipe_count"] for row in rows]

        assert rows
        assert counts == sorted(counts, reverse=True)

    async def test_my_recipes_respect_the_match_rate_floor(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """하한을 올리면 덜 맞는 레시피가 빠져야 합니다."""
        rows = await fetch(
            db_conn,
            "my_recipe_candidates",
            {"user_id": seeded.user, "min_match_rate": 1.0, "max_results": 100},
        )

        assert all(float(row["match_rate"]) == 1.0 for row in rows)

    async def test_multi_primary_product_is_one_fridge_row(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """PRIMARY 가 둘이어도 냉장고 한 칸은 한 줄입니다. 재료만 배열로 늘어납니다."""
        await db_conn.execute(
            text(
                "INSERT INTO product_ingredient (product_id, ingredient_id, role) "
                "VALUES (:product_id, :ingredient_id, 'PRIMARY')"
            ),
            {"product_id": seeded.kimchi_a, "ingredient_id": seeded.pork},
        )

        rows = await fetch(db_conn, "my_fridge_items", {"user_id": seeded.user})
        kimchi = next(row for row in rows if row["product_id"] == seeded.kimchi_a)

        assert len([row for row in rows if row["product_id"] == seeded.kimchi_a]) == 1
        assert len(kimchi["ingredients"]) == 2


class TestRecommendationPriorityOrdering:
    """추천 상품 우선순위가 첫 정렬 기준입니다.

    데모·운영이 밀고 싶은 레시피는 seed 가 `recipe_product.recommendation_priority` 를 넣고,
    SQL 은 레시피 id 를 모릅니다. 시드 사용자는 김치·돼지고기를 갖고 있어 김치찌개와
    삼겹살구이가 둘 다 매칭률 1.0 이고, 조리시간이 짧은 김치찌개가 앞섭니다.
    """

    async def test_priority_beats_match_rate(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """매칭률이 낮아도 우선순위가 높으면 앞에 옵니다."""
        # 삼겹살구이에 만료된 두부를 필수로 붙여 매칭률을 떨어뜨립니다.
        await db_conn.execute(
            text(
                "INSERT INTO recipe_ingredient (recipe_id, ingredient_id, quantity, unit, is_required) "
                "VALUES (:recipe_id, :ingredient_id, 100, 'g', TRUE)"
            ),
            {"recipe_id": seeded.pork_grill, "ingredient_id": seeded.tofu},
        )
        await db_conn.execute(
            text(
                "INSERT INTO recipe_product (recipe_id, ingredient_id, product_id, recommendation_priority) "
                "VALUES (:recipe_id, :ingredient_id, :product_id, 100)"
            ),
            {"recipe_id": seeded.pork_grill, "ingredient_id": seeded.pork, "product_id": seeded.pork_a},
        )

        rows = await fetch(
            db_conn, "my_recipe_candidates", {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100}
        )
        by_id = {row["recipe_id"]: row for row in rows}

        assert rows[0]["recipe_id"] == seeded.pork_grill
        assert float(by_id[seeded.pork_grill]["match_rate"]) < float(by_id[seeded.kimchi_stew]["match_rate"])

    async def test_without_priority_match_rate_decides(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """우선순위가 없으면 기존 순서(매칭률 -> 부족 수 -> 조리시간)가 그대로입니다."""
        rows = await fetch(
            db_conn, "my_recipe_candidates", {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100}
        )
        order = [row["recipe_id"] for row in rows]

        assert order[:2] == [seeded.kimchi_stew, seeded.pork_grill]

    async def test_priority_does_not_add_a_column(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """정렬만 바꿉니다. 컬럼 집합은 추천 이유 생성에 필요한 세 목록까지로 고정합니다."""
        rows = await fetch(
            db_conn, "my_recipe_candidates", {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 1}
        )

        assert set(rows[0]) == {
            "recipe_id",
            "name",
            "image_url",
            "difficulty",
            "cook_time_min",
            "servings",
            "required_count",
            "available_count",
            "missing_count",
            "match_rate",
            "missing_ingredients",
            "held_ingredients",
            "pantry_ingredients",
        }

    async def test_multiple_designated_products_count_once(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """한 레시피에 지정 상품이 여럿이어도 레시피는 한 번만 나옵니다. 집계를 레시피당 한 번 하기 때문입니다."""
        for product_id in (seeded.tofu_a, seeded.tofu_b):
            await db_conn.execute(
                text(
                    "INSERT INTO recipe_product (recipe_id, ingredient_id, product_id, recommendation_priority) "
                    "VALUES (:recipe_id, :ingredient_id, :product_id, 100) ON CONFLICT DO NOTHING"
                ),
                {"recipe_id": seeded.kimchi_stew, "ingredient_id": seeded.tofu, "product_id": product_id},
            )

        rows = await fetch(
            db_conn, "my_recipe_candidates", {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100}
        )
        ids = [row["recipe_id"] for row in rows]

        assert ids[0] == seeded.kimchi_stew
        assert ids.count(seeded.kimchi_stew) == 1


class TestIngredientHierarchyAcrossQueries:
    """child 보유는 parent 요구를 채우고, 그 반대는 아닙니다. 상세·상품 쿼리도 같은 규칙입니다."""

    async def test_recipe_detail_marks_parent_as_in_fridge(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """목심 상품을 가진 사용자의 상세 화면에서 돼지고기는 보유입니다."""
        await seed_child_ingredient(
            db_conn,
            ingredient_id=seeded.pork + 1000,
            name="목심",
            parent_id=seeded.pork,
            repoint_product_id=seeded.pork_a,
        )

        rows = await fetch(
            db_conn,
            "recipe_missing_ingredients",
            {"recipe_id": seeded.kimchi_stew, "base_product_id": 0, "user_id": seeded.user},
        )
        status = {row["ingredient_id"]: row["status"] for row in rows}

        assert status[seeded.pork] == "IN_FRIDGE"

    async def test_product_recipes_reach_parent_requirements(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """목심 상품으로 만들 수 있는 요리에 돼지고기를 요구하는 레시피가 들어옵니다."""
        await seed_child_ingredient(
            db_conn,
            ingredient_id=seeded.pork + 1000,
            name="목심",
            parent_id=seeded.pork,
            repoint_product_id=seeded.pork_a,
        )

        rows = await fetch(db_conn, "product_recipes", {"product_id": seeded.pork_a, "max_results": 50, "skip": 0})
        recipe_ids = {row["recipe_id"] for row in rows}

        assert {seeded.kimchi_stew, seeded.pork_grill} <= recipe_ids

    async def test_hierarchy_stops_at_the_parent(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """계층은 1단계까지만 봅니다. 상품 PRIMARY 를 돼지고기 -> 목심 -> 목심 슬라이스로 두 번 옮깁니다.

        손자 상품을 갖고 있으면 부모(목심)는 보유지만 조부모(돼지고기)는 아닙니다.

        재귀로 끝까지 올라가지 않는 것이 정책입니다. 2단계가 필요해지면 데이터가 아니라
        쿼리 규칙을 바꿔야 하고, 이 테스트가 그때 깨집니다.
        """
        pork_neck = await seed_child_ingredient(
            db_conn,
            ingredient_id=seeded.pork + 1000,
            name="목심",
            parent_id=seeded.pork,
            repoint_product_id=seeded.pork_a,
        )
        await seed_child_ingredient(
            db_conn,
            ingredient_id=seeded.pork + 2000,
            name="목심 슬라이스",
            parent_id=pork_neck,
            repoint_product_id=seeded.pork_a,
        )

        rows = await fetch(
            db_conn, "my_recipe_candidates", {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100}
        )
        stew = next(row for row in rows if row["recipe_id"] == seeded.kimchi_stew)

        assert seeded.pork in {item["ingredient_id"] for item in stew["missing_ingredients"]}

    async def test_parent_in_fridge_does_not_cover_child_requirement(
        self, db_conn: AsyncConnection, seeded: SeedIds
    ) -> None:
        """돼지고기 상품만 있으면 목심을 요구하는 레시피는 부족입니다."""
        pork_neck = await seed_child_ingredient(
            db_conn, ingredient_id=seeded.pork + 1000, name="목심", parent_id=seeded.pork
        )
        await db_conn.execute(
            text(
                "UPDATE recipe_ingredient SET ingredient_id = :child_id "
                "WHERE recipe_id = :recipe_id AND ingredient_id = :parent_id"
            ),
            {"child_id": pork_neck, "recipe_id": seeded.pork_grill, "parent_id": seeded.pork},
        )

        rows = await fetch(
            db_conn, "my_recipe_candidates", {"user_id": seeded.user, "min_match_rate": 0.0, "max_results": 100}
        )
        grill = next((row for row in rows if row["recipe_id"] == seeded.pork_grill), None)

        assert grill is None or grill["missing_count"] == 1
