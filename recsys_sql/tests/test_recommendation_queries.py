"""추천 비즈니스 로직 SQL 검증.

시드 데이터를 넣은 트랜잭션 안에서 실제 쿼리를 돌리고 규칙이 지켜지는지 확인합니다.
테스트가 끝나면 롤백되므로 DB 에 남는 것이 없습니다.
"""

from __future__ import annotations

from typing import Any

import pytest
from recsys_fixtures import SeedIds
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import QUERIES_DIR, get_settings
from recsys_sql.runner import explain_query, run_query

TEMPLATE_DIR = QUERIES_DIR / "_template"
QUERY_DIR = QUERIES_DIR / "openLeeWorld"

pytestmark = pytest.mark.db


def template_query(name: str) -> SqlQuery:
    """예시 폴더에서 쿼리 하나를 꺼냅니다."""
    return next(query for query in load_catalog(TEMPLATE_DIR) if query.name == name)


async def fetch(conn: AsyncConnection, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """쿼리를 돌리고 dict 목록으로 받습니다."""
    return (await run_query(conn, template_query(name), params)).as_dicts()


class TestFridgeRecipeMatch:
    """냉장고 기반 레시피 추천 규칙."""

    async def test_ranks_fully_covered_recipe_first(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """필수 재료를 다 갖춘 레시피가 커버리지 1.0 으로 먼저 나옵니다."""
        rows = await fetch(
            db_conn,
            "fridge_recipe_match",
            {"user_id": seeded.user, "min_coverage": 0.1, "max_results": 10},
        )
        seed_rows = [row for row in rows if row["recipe_id"] in seeded.recipes]

        assert seed_rows, "시드 레시피가 하나도 추천되지 않았습니다."
        assert seed_rows[0]["recipe_id"] == seeded.kimchi_stew
        assert float(seed_rows[0]["coverage"]) == 1.0
        assert seed_rows[0]["missing_count"] == 0

    async def test_pantry_ingredient_counts_as_owned(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """소금은 냉장고에 없지만 상비재료라 보유한 것으로 칩니다."""
        rows = await fetch(
            db_conn,
            "fridge_recipe_match",
            {"user_id": seeded.user, "min_coverage": 0.1, "max_results": 10},
        )
        grill = next(row for row in rows if row["recipe_id"] == seeded.pork_grill)

        assert float(grill["coverage"]) == 1.0
        assert grill["missing_count"] == 0

    async def test_expired_ingredient_does_not_count(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """유통기한이 지난 두부는 보유하지 않은 것으로 처리해야 합니다.

        두부조림의 필수 재료는 두부와 참기름입니다. 두부는 냉장고에 있지만 기한이 지났고
        참기름은 아예 없으므로, 냉장고 재료를 하나도 쓰지 않는 레시피가 됩니다.
        그래서 후보 자체에서 빠집니다. 같은 요청에서 나머지 시드 레시피는 나오므로
        상한(`max_results`) 때문에 잘린 것이 아닙니다.
        """
        rows = await fetch(
            db_conn,
            "fridge_recipe_match",
            {"user_id": seeded.user, "min_coverage": 0.0, "max_results": 500},
        )
        recipe_ids = {row["recipe_id"] for row in rows}

        assert seeded.kimchi_stew in recipe_ids, "기한이 남은 재료를 쓰는 레시피는 나와야 합니다"
        assert seeded.tofu_braise not in recipe_ids

    async def test_min_coverage_filters_out_low_matches(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """커버리지 하한을 올리면 두부조림이 빠집니다."""
        rows = await fetch(
            db_conn,
            "fridge_recipe_match",
            {"user_id": seeded.user, "min_coverage": 0.5, "max_results": 50},
        )
        recipe_ids = {row["recipe_id"] for row in rows}

        assert seeded.kimchi_stew in recipe_ids
        assert seeded.tofu_braise not in recipe_ids

    async def test_cook_time_breaks_the_tie(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """커버리지가 같으면 조리 시간이 짧은 쪽이 먼저 나옵니다."""
        rows = await fetch(
            db_conn,
            "fridge_recipe_match",
            {"user_id": seeded.user, "min_coverage": 1.0, "max_results": 50},
        )
        ordered = [row["recipe_id"] for row in rows if row["recipe_id"] in seeded.recipes]

        assert ordered.index(seeded.kimchi_stew) < ordered.index(seeded.pork_grill)


class TestMissingIngredientProducts:
    """부족한 재료를 채울 상품 추천 규칙."""

    async def test_recipe_designated_product_wins(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """recipe_product 로 지정된 상품이 더 싼 상품보다 먼저 나옵니다."""
        rows = await fetch(
            db_conn,
            "missing_ingredient_products",
            {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "max_per_ingredient": 3},
        )
        tofu_rows = [row for row in rows if row["ingredient_id"] == seeded.tofu]

        assert [row["product_id"] for row in tofu_rows] == [seeded.tofu_a, seeded.tofu_b]

    async def test_inactive_and_out_of_stock_products_are_excluded(
        self, db_conn: AsyncConnection, seeded: SeedIds
    ) -> None:
        """비활성 상품과 품절 상품은 후보에 없어야 합니다."""
        rows = await fetch(
            db_conn,
            "missing_ingredient_products",
            {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "max_per_ingredient": 10},
        )
        product_ids = {row["product_id"] for row in rows}

        assert seeded.tofu_c not in product_ids, "비활성 상품이 추천되었습니다."
        assert seeded.sesame_a not in product_ids, "품절 상품이 추천되었습니다."

    async def test_popularity_beats_price(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """지정 상품이 없으면 인기도가 높은 쪽이 더 비싸도 먼저 나옵니다."""
        rows = await fetch(
            db_conn,
            "missing_ingredient_products",
            {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "max_per_ingredient": 3},
        )
        sesame_rows = [row for row in rows if row["ingredient_id"] == seeded.sesame_oil]

        assert [row["product_id"] for row in sesame_rows] == [seeded.sesame_c, seeded.sesame_b]

    async def test_max_per_ingredient_is_applied(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """재료별 상한이 재료마다 따로 적용됩니다."""
        rows = await fetch(
            db_conn,
            "missing_ingredient_products",
            {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "max_per_ingredient": 1},
        )
        per_ingredient: dict[int, int] = {}
        for row in rows:
            per_ingredient[row["ingredient_id"]] = per_ingredient.get(row["ingredient_id"], 0) + 1

        assert set(per_ingredient) == {seeded.tofu, seeded.sesame_oil}
        assert all(count == 1 for count in per_ingredient.values())


class TestReorderCandidates:
    """재구매 후보 추천 규칙."""

    async def test_only_stale_purchases_are_candidates(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """최근에 산 상품(10일 전)은 30일 기준에서 빠집니다."""
        rows = await fetch(
            db_conn,
            "reorder_candidates",
            {"user_id": seeded.user, "days_since": 30, "max_results": 50},
        )
        product_ids = {row["product_id"] for row in rows}

        assert seeded.tofu_b not in product_ids

    async def test_item_still_fresh_in_fridge_is_excluded(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """냉장고에 멀쩡히 있는 김치A 는 다시 사라고 하지 않습니다."""
        rows = await fetch(
            db_conn,
            "reorder_candidates",
            {"user_id": seeded.user, "days_since": 30, "max_results": 50},
        )
        assert seeded.kimchi_a not in {row["product_id"] for row in rows}

    async def test_expired_fridge_item_is_a_candidate(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """냉장고에 있어도 유통기한이 지났으면 재구매 후보가 됩니다."""
        rows = await fetch(
            db_conn,
            "reorder_candidates",
            {"user_id": seeded.user, "days_since": 30, "max_results": 50},
        )
        assert seeded.tofu_a in {row["product_id"] for row in rows}

    async def test_out_of_stock_is_excluded(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """품절 상품은 재구매 후보에서 뺍니다."""
        rows = await fetch(
            db_conn,
            "reorder_candidates",
            {"user_id": seeded.user, "days_since": 30, "max_results": 50},
        )
        assert seeded.sesame_a not in {row["product_id"] for row in rows}


class TestExecutionPlan:
    """실행계획 점검. 큰 테이블 풀스캔이 끼면 실패시킵니다."""

    @pytest.mark.parametrize(
        ("query_name", "params"),
        [
            ("fridge_recipe_match", {"user_id": 1, "min_coverage": 0.5, "max_results": 10}),
            ("missing_ingredient_products", {"user_id": 1, "recipe_id": 1, "max_per_ingredient": 3}),
            ("reorder_candidates", {"user_id": 1, "days_since": 30, "max_results": 10}),
        ],
    )
    async def test_no_forbidden_sequential_scan(
        self, db_conn: AsyncConnection, query_name: str, params: dict[str, Any]
    ) -> None:
        """FORBID_SEQ_SCAN_ON 에 적힌 테이블은 인덱스로 접근해야 합니다.

        표가 작을 때는 플래너가 순차 읽기를 고르는 편이 맞습니다.
        `SEQ_SCAN_ROW_LIMIT` 를 넘는 규모에서만 실패시킵니다.
        """
        settings = get_settings()
        report = await explain_query(db_conn, template_query(query_name), params)
        forbidden = report.forbidden_seq_scans(settings.forbid_seq_scan_on, row_limit=settings.seq_scan_row_limit)

        assert not forbidden, (
            f"{query_name}: 금지된 Seq Scan {forbidden} "
            f"(추정 행수 { {table: report.seq_scan_rows[table] for table in forbidden} }, 인덱스를 확인하세요)"
        )

    async def test_query_finishes_within_timeout(self, db_conn: AsyncConnection, seeded: SeedIds) -> None:
        """statement_timeout 안에 끝나야 합니다. 넘으면 run_query 가 예외를 냅니다."""
        result = await run_query(
            db_conn,
            template_query("fridge_recipe_match"),
            {"user_id": seeded.user, "min_coverage": 0.1, "max_results": 10},
        )
        assert result.elapsed_ms < get_settings().query_timeout_seconds * 1000


class TestProductIngredientRole:
    """보유 판정과 상품 추천이 같은 role 을 봐야 합니다."""

    async def test_secondary_ingredient_product_is_not_recommended(
        self, db_conn: AsyncConnection, seeded: SeedIds
    ) -> None:
        """SECONDARY 로만 걸린 상품을 추천하면, 담아도 재료가 계속 부족합니다.

        냉장고 쪽은 PRIMARY 만 보유로 인정합니다. 추천 쪽이 role 을 안 보면
        "사라고 해서 샀는데 여전히 부족" 한 상태가 반복됩니다.
        """
        await db_conn.execute(
            text(
                "INSERT INTO product_ingredient (product_id, ingredient_id, role) "
                "VALUES (:product_id, :ingredient_id, 'SECONDARY')"
            ),
            {"product_id": seeded.kimchi_a, "ingredient_id": seeded.sesame_oil},
        )

        rows = await fetch(
            db_conn,
            "missing_ingredient_products",
            {"user_id": seeded.user, "recipe_id": seeded.tofu_braise, "max_per_ingredient": 10},
        )
        sesame_products = {row["product_id"] for row in rows if row["ingredient_id"] == seeded.sesame_oil}

        assert seeded.kimchi_a not in sesame_products, "SECONDARY 로 걸린 상품이 추천되었습니다."
        assert sesame_products, "PRIMARY 상품까지 같이 빠지면 안 됩니다."


class TestBufferBudget:
    """Neon 에서는 버퍼 블록 수가 그대로 네트워크 왕복이 됩니다.

    계산 노드와 스토리지가 분리돼 있어, 캐시가 식으면 `shared hit` 이 `shared read` 로
    바뀝니다(`recsys_sql/docs/postgresql_neon_explain_checklist.md` 3절).
    그래서 시간보다 블록 수가 회귀를 정직하게 보여 줍니다.

    홈 화면 첫 쿼리가 한때 5행을 내는 데 **232,931 블록**을 읽었습니다. 버블 규칙 해석이
    (버블 x 레시피) 쌍마다 재료 집계를 다시 돌린 탓이었고, alembic 0010 에서 레시피당
    한 번만 집계하도록 고쳤습니다(416 블록). 다시 그 모양으로 돌아가지 않게 막습니다.

    버퍼 수치는 EXPLAIN **루트 노드** 값입니다. PostgreSQL 은 상위 노드에 하위 값을 누적해
    보고하므로 루트가 곧 트리 전체의 합입니다. 노드를 재귀로 더하면 같은 블록을 깊이만큼
    다시 세게 됩니다(실제로 9배 넘게 부풀었습니다).
    """

    # 옛 뷰의 값(232,931 / 46,698 / 49,645)은 전부 넘고, 지금 값(416 / 415 / 5,375)에는
    # 네 배쯤 여유가 있는 선입니다. 데이터가 커지면 다시 재고 조정하세요.
    BUDGET = 20_000

    @pytest.mark.parametrize(
        ("query_name", "params"),
        [
            ("bubble_candidate_counts", {}),
            ("bubble_recipe_candidates", {"keyword_id": "MEAT", "max_results": 50}),
            ("bubble_products", {"keyword_id": "MEAT", "max_results": 20, "skip": 0}),
        ],
    )
    async def test_bubble_queries_stay_within_budget(
        self, db_conn: AsyncConnection, query_name: str, params: dict[str, Any]
    ) -> None:
        """버블 쿼리 3종은 같은 뷰를 봅니다. 뷰가 무거워지면 셋 다 같이 무거워집니다."""
        query = next(query for query in load_catalog(QUERY_DIR) if query.name == query_name)
        report = await explain_query(db_conn, query, params, analyze=True)

        assert report.shared_blocks, "BUFFERS 가 안 붙었습니다"
        assert report.shared_blocks < self.BUDGET, (
            f"{query_name}: 버퍼 {report.shared_blocks:,} 블록. 예산 {self.BUDGET:,}. "
            "뷰가 레시피당 한 번이 아니라 쌍마다 집계하고 있는지 확인하세요."
        )
