"""버블 UI 쿼리 검증.

버블은 눌렀을 때 결과가 비면 안 됩니다. 데이터가 바뀌면 후보가 줄 수 있어
`min_candidates` 를 실제로 넘는지 여기서 고정합니다.

시드 데이터를 넣지 않고 **실제 적재분**을 봅니다. 버블은 전체 카탈로그를 대상으로
동작해야 의미가 있고, 시드 몇 건으로는 규칙이 지켜지는지 알 수 없기 때문입니다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, load_catalog
from recsys_sql.config import QUERIES_DIR
from recsys_sql.runner import run_query

QUERY_DIR = QUERIES_DIR / "openLeeWorld"

pytestmark = pytest.mark.db


def bubble_query(name: str) -> SqlQuery:
    """버블 쿼리 하나를 꺼냅니다."""
    return next(query for query in load_catalog(QUERY_DIR) if query.name == name)


async def fetch(conn: AsyncConnection, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """쿼리를 돌리고 dict 목록으로 받습니다."""
    return (await run_query(conn, bubble_query(name), params)).as_dicts()


async def test_every_active_bubble_meets_min_candidates(db_conn: AsyncConnection) -> None:
    """후보가 min_candidates 미만인 버블은 화면에 내면 안 됩니다.

    비어 있는 버블을 누르게 하는 것이 가장 나쁜 경험입니다.
    """
    rows = await fetch(db_conn, "bubble_candidate_counts", {})
    assert rows, "활성 버블이 하나도 없습니다"

    starved = [row for row in rows if not row["is_servable"]]
    detail = ", ".join(f"{row['keyword_id']}={row['candidates']}/{row['min_candidates']}" for row in starved)
    assert not starved, f"후보가 모자란 버블이 있습니다: {detail}"


async def test_bubble_candidates_return_recipes(db_conn: AsyncConnection) -> None:
    """버블마다 실제로 레시피가 나와야 합니다."""
    for row in await fetch(db_conn, "bubble_candidate_counts", {}):
        recipes = await fetch(db_conn, "bubble_recipe_candidates", {"keyword_id": row["keyword_id"], "max_results": 5})
        assert recipes, f"{row['keyword_id']} 버블에서 레시피가 나오지 않습니다"
        assert all(item["name"] for item in recipes)


async def test_low_ingredient_bubble_respects_its_threshold(db_conn: AsyncConnection) -> None:
    """`재료 적게 드는 요리` 는 임계값을 실제로 지켜야 합니다."""
    recipes = await fetch(db_conn, "bubble_recipe_candidates", {"keyword_id": "LOW_INGREDIENT", "max_results": 30})

    assert recipes
    assert all(item["ingredient_count"] <= 5 for item in recipes), "임계값을 넘는 레시피가 섞였습니다"


async def test_quick_bubble_respects_cook_time(db_conn: AsyncConnection) -> None:
    """`15분 안에 끝나는 요리` 에 조리시간이 없는 레시피가 섞이면 안 됩니다."""
    recipes = await fetch(db_conn, "bubble_recipe_candidates", {"keyword_id": "QUICK_15MIN", "max_results": 30})

    assert recipes
    assert all(item["cook_time_min"] is not None and item["cook_time_min"] <= 15 for item in recipes)


async def test_meat_bubble_excludes_egg_only_desserts(db_conn: AsyncConnection) -> None:
    """`고기 든든하게` 규칙에 `난류` 를 넣었더니 `레몬 커드` 가 걸렸습니다.

    육류만 보도록 고쳤고, 그 결정을 여기서 고정합니다.
    """
    recipes = await fetch(db_conn, "bubble_recipe_candidates", {"keyword_id": "MEAT", "max_results": 50})
    names = {item["name"] for item in recipes}

    assert recipes
    assert "레몬 커드" not in names
