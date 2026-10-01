"""serving HTTP 통합 테스트. 실제 PostgreSQL 위에서 요청부터 응답까지 한 번에 봅니다.

supertest 처럼 앱 전체(미들웨어 -> 라우터 -> 카탈로그 SQL -> 응답 스키마)를 HTTP 로 부르고
상태 코드, envelope, 본문을 이어서 확인합니다. 단위 테스트가 가짜 풀로 흉내 내는 판정을
여기서는 실제 스키마와 SQL 이 합니다.

시드, 롤백 세계, 클라이언트는 `api_test_harness.py` 에 있습니다. 여기에는 무엇을 보는지만 둡니다.

- 테스트마다 트랜잭션을 열고 끝나면 롤백합니다. 공용 dev 브랜치에 아무것도 남지 않습니다.
- 시드로 결과를 순서·값까지 단정합니다. 버블과 보관 가이드처럼 적재 데이터가 입력인 API 만 성질을 봅니다.
- 실제 OpenRouter 경로는 맨 아래 `llm` 테스트 하나가 보며, `--run-llm` 을 줄 때만 돕니다.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import asyncpg
import httpx
import pytest
from api_test_harness import World, connect, in_days, open_world, stock_fridge

from serving import fridge_sql
from serving.config import Settings
from serving.reason_runtime import ReasonRuntime
from serving.routers.fridge import DUPLICATE_ITEM, INACTIVE_PRODUCT, NO_PRIMARY_INGREDIENT

pytestmark = pytest.mark.db


@pytest.fixture
async def world() -> AsyncIterator[World]:
    """시드를 넣은 트랜잭션 위의 앱. 테스트가 끝나면 전부 롤백합니다."""
    async with open_world() as opened:
        yield opened


def _by_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {item["recipe_id"]: item for item in items}


# --- RECO-02 My냉장고 레시피 추천 --------------------------------------------


async def test_my_recipes_ranks_recipes_from_fridge(world: World) -> None:
    """냉장고(김치, 목심)로 만들 수 있는 레시피를 매칭률 순으로 냅니다.

    - 목심(자식) 상품이 돼지고기(부모)를 요구하는 레시피를 채웁니다(재료 계층).
    - 소금은 상비재료라 냉장고에 없어도 보유로 칩니다.
    - 매칭률 0.25 인 레시피는 하한(0.5) 미달이라 빠지고, 냉장고 재료를 안 쓰는 레시피는 후보가 아닙니다.
    """
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a, ids.neck_a)

    items = (await world.api.get("/recommendations/my-recipes", user=ids.me, limit=10)).expect(200).data["items"]

    assert [item["recipe_id"] for item in items] == [ids.grill, ids.stew]
    grill, stew = items
    assert grill["match"] == {
        "required_ingredients": 2,
        "available_ingredients": 2,
        "missing_ingredients": 0,
        "match_rate": 1.0,
    }
    assert grill["missing_ingredients"] == []
    assert stew["match"] == {
        "required_ingredients": 3,
        "available_ingredients": 2,
        "missing_ingredients": 1,
        "match_rate": 0.667,
    }
    assert stew["missing_ingredients"] == [{"ingredient_id": ids.tofu, "name": "두부"}]
    assert "두부" in stew["recommendation_reason"]
    assert all(item["recommendation_reason"] for item in items)


async def test_my_recipes_ignores_expired_items_until_expiry_is_cleared(world: World) -> None:
    """기한 지난 품목은 보유로 치지 않습니다. 수정으로 기한을 지우면 바로 추천에 반영됩니다.

    기한을 지운 뒤에는 매칭률이 같은 레시피가 조리시간 순으로 서고, 매칭률이 정확히 0.5 인 레시피가
    하한 경계에서 들어옵니다.
    """
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a, ids.neck_a)
    await world.api.post(
        "/users/me/fridge",
        {"product_id": ids.tofu_a, "quantity": 1, "unit": "모", "expires_at": in_days(-1)},
        user=ids.me,
    )

    fridge = (await world.api.get("/users/me/fridge", user=ids.me)).expect(200).data["items"]
    expired = {item["product"]["product_id"]: item["is_expired"] for item in fridge}
    assert expired == {ids.kimchi_a: False, ids.neck_a: False, ids.tofu_a: True}

    before = (await world.api.get("/recommendations/my-recipes", user=ids.me)).expect(200).data["items"]
    assert _by_id(before)[ids.stew]["missing_ingredients"] == [{"ingredient_id": ids.tofu, "name": "두부"}]

    (await world.api.patch(f"/users/me/fridge/{ids.tofu_a}", {"expires_at": None}, user=ids.me)).expect(200)

    after = (await world.api.get("/recommendations/my-recipes", user=ids.me)).expect(200).data["items"]
    assert [item["recipe_id"] for item in after] == [ids.stew, ids.grill, ids.far]
    assert _by_id(after)[ids.stew]["match"]["match_rate"] == 1.0
    assert _by_id(after)[ids.far]["match"]["match_rate"] == 0.5
    assert {m["name"] for m in _by_id(after)[ids.far]["missing_ingredients"]} == {"대파", "참기름"}


async def test_my_recipes_respects_limit_and_empty_fridge(world: World) -> None:
    """limit 만큼만 내고, 냉장고가 빈 사용자는 200 + 빈 목록입니다."""
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a, ids.neck_a)

    top = (await world.api.get("/recommendations/my-recipes", user=ids.me, limit=1)).expect(200).data["items"]
    assert [item["recipe_id"] for item in top] == [ids.grill]

    empty = (await world.api.get("/recommendations/my-recipes", user=ids.other)).expect(200).data
    assert empty == {"items": []}


# --- RECIPE-01 / RECIPE-03 레시피 --------------------------------------------


async def test_recipe_detail(world: World) -> None:
    """재료줄과 단계를 함께 냅니다. 단계는 번호 순, 상비재료 표시가 실립니다. 없는 레시피는 404."""
    ids = world.ids

    stew = (await world.api.get(f"/recipes/{ids.stew}")).expect(200).data
    assert stew["name"] == "시드 김치찌개"
    assert {line["name"] for line in stew["ingredients"]} == {"배추김치", "돼지고기", "두부"}
    assert all(line["is_required"] for line in stew["ingredients"])
    assert [step["step_no"] for step in stew["steps"]] == [1, 2]
    assert stew["steps"][0]["instruction"] == "돼지고기를 볶습니다."

    grill = (await world.api.get(f"/recipes/{ids.grill}")).expect(200).data
    assert {line["name"]: line["is_pantry"] for line in grill["ingredients"]} == {"돼지고기": False, "소금": True}
    assert grill["steps"] == []

    (await world.api.get(f"/recipes/{ids.absent}")).expect(404)


async def test_missing_products_uses_fridge_and_ranks_by_price(world: World) -> None:
    """로그인하면 냉장고 재료를 빼고, 부족 재료마다 싼 상품부터 냅니다. 판매 중지 상품은 빠집니다."""
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a, ids.neck_a)

    data = (await world.api.get(f"/recipes/{ids.stew}/missing-products", user=ids.me)).expect(200).data

    assert data["recipe_id"] == ids.stew
    assert data["missing_ingredients"] == [
        {
            "ingredient_id": ids.tofu,
            "name": "두부",
            "products": [
                {"product_id": ids.tofu_b, "name": "두부B", "price": 2500.0, "rank": 1},
                {"product_id": ids.tofu_a, "name": "두부A", "price": 3000.0, "rank": 2},
            ],
        }
    ]


async def test_missing_products_anonymous_never_substitutes_child_ingredient(world: World) -> None:
    """비로그인은 냉장고 없이 계산합니다. 돼지고기 자리에 목심 상품을 대신 추천하지 않습니다."""
    ids = world.ids

    data = (await world.api.get(f"/recipes/{ids.stew}/missing-products")).expect(200).data

    products = {m["name"]: [p["product_id"] for p in m["products"]] for m in data["missing_ingredients"]}
    assert set(products) == {"배추김치", "돼지고기", "두부"}
    assert products["돼지고기"] == [ids.mealkit]
    assert ids.neck_a not in products["돼지고기"]
    assert products["배추김치"] == [ids.kimchi_a, ids.mealkit]


async def test_missing_products_filters_and_base_product(world: World) -> None:
    """품절 상품과 살 수 없는 재료는 빠지고, 재료당 개수를 지키며, 기준 상품이 채우는 재료는 부족이 아닙니다."""
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a, ids.neck_a)

    data = (
        (await world.api.get(f"/recipes/{ids.tofu_dish}/missing-products", user=ids.me, max_per_ingredient=1))
        .expect(200)
        .data
    )
    products = {m["name"]: [p["product_id"] for p in m["products"]] for m in data["missing_ingredients"]}
    # 참기름은 파는 상품이 없어 목록에 없습니다. 대파B 는 재고 0 이라 빠집니다.
    assert products == {"두부": [ids.tofu_b], "대파": [ids.onion_a]}

    covered = (
        (await world.api.get(f"/recipes/{ids.stew}/missing-products", user=ids.me, base_product_id=ids.tofu_a))
        .expect(200)
        .data
    )
    assert covered["missing_ingredients"] == []

    (await world.api.get(f"/recipes/{ids.absent}/missing-products", user=ids.me)).expect(404)


# --- PROD-01~03 상품 ---------------------------------------------------------


async def test_product_detail(world: World) -> None:
    """상품 상세와 구성 재료. PRIMARY 가 둘인 밀키트는 재료 둘. 없는 상품은 404."""
    ids = world.ids

    tofu = (await world.api.get(f"/products/{ids.tofu_a}")).expect(200).data
    assert tofu["name"] == "두부A"
    assert tofu["price"] == 3000.0
    assert tofu["storage_type"] == "냉장"
    assert tofu["ingredients"] == [{"ingredient_id": ids.tofu, "name": "두부"}]
    assert "is_active" not in tofu

    mealkit = (await world.api.get(f"/products/{ids.mealkit}")).expect(200).data
    assert {i["name"] for i in mealkit["ingredients"]} == {"배추김치", "돼지고기"}

    bare = (await world.api.get(f"/products/{ids.bare}")).expect(200).data
    assert bare["ingredients"] == []

    (await world.api.get(f"/products/{ids.absent}")).expect(404)


async def test_product_recipes_order_by_missing_then_cook_time(world: World) -> None:
    """상품 하나로 만들 수 있는 레시피를 부족 재료 수, 조리시간 순으로 냅니다. 레시피가 없으면 빈 목록."""
    ids = world.ids

    items = (await world.api.get(f"/products/{ids.tofu_a}/recipes")).expect(200).data["items"]

    assert [item["recipe_id"] for item in items] == [ids.tofu_dish, ids.stew, ids.far]
    summaries = {item["recipe_id"]: item["ingredient_summary"] for item in items}
    assert summaries[ids.tofu_dish] == {"total_count": 3, "matched_count": 1, "missing_count": 2}
    assert summaries[ids.far] == {"total_count": 4, "matched_count": 1, "missing_count": 3}

    limited = (await world.api.get(f"/products/{ids.tofu_a}/recipes", limit=1)).expect(200).data["items"]
    assert [item["recipe_id"] for item in limited] == [ids.tofu_dish]

    none = (await world.api.get(f"/products/{ids.bare}/recipes")).expect(200).data
    assert none == {"items": []}


async def test_storage_guide_is_hidden_for_multi_or_missing_primary(world: World) -> None:
    """PRIMARY 가 둘인 상품(밀키트)과 재료 미연결 상품은 보관 가이드가 404 입니다."""
    ids = world.ids

    (await world.api.get(f"/products/{ids.mealkit}/storage-guide")).expect(404)
    (await world.api.get(f"/products/{ids.bare}/storage-guide")).expect(404)
    (await world.api.get(f"/products/{ids.absent}/storage-guide")).expect(404)


async def test_storage_guide_on_loaded_catalog(world: World) -> None:
    """적재된 지침으로 확인합니다. (장소, 상황)은 한 줄씩이고, 상품 보관 장소가 있으면 그 장소만 나옵니다."""
    product = await world.conn.fetchrow(
        """
        SELECT p.product_id, p.storage_type
        FROM product p
        JOIN product_ingredient pi ON pi.product_id = p.product_id AND pi.role = 'PRIMARY'
        JOIN ingredient i          ON i.ingredient_id = pi.ingredient_id
        WHERE (SELECT count(*) FROM product_ingredient x
               WHERE x.product_id = p.product_id AND x.role = 'PRIMARY') = 1
          AND p.storage_type IS NOT NULL
          AND EXISTS (SELECT 1 FROM storage_guideline sg
                      WHERE sg.ingredient_id IN (i.ingredient_id, i.parent_ingredient_id)
                        AND sg.storage_location = p.storage_type)
        ORDER BY p.product_id
        LIMIT 1
        """
    )
    if product is None:
        pytest.skip("보관 지침이 붙은 상품이 적재되어 있지 않습니다")

    guide = (await world.api.get(f"/products/{product['product_id']}/storage-guide")).expect(200).data

    assert guide["product_id"] == product["product_id"]
    assert guide["items"]
    pairs = [(item["storage_location"], item["storage_context"]) for item in guide["items"]]
    assert len(pairs) == len(set(pairs)), "같은 (장소, 상황)이 두 줄 이상 나왔습니다"
    assert {location for location, _ in pairs} == {product["storage_type"]}


# --- FRIDGE-01~04 My냉장고 ---------------------------------------------------


async def test_fridge_post_contract(world: World) -> None:
    """담기는 요청 값을 돌려주고, 거절 네 가지는 상태와 메시지로 구분됩니다."""
    ids = world.ids
    body = {"product_id": ids.kimchi_a, "quantity": 500, "unit": "g"}

    created = (await world.api.post("/users/me/fridge", body, user=ids.me)).expect(200).data
    assert created == {"product_id": ids.kimchi_a, "quantity": 500.0, "unit": "g", "expires_at": None}

    (await world.api.post("/users/me/fridge", body, user=ids.me)).expect(409, message=DUPLICATE_ITEM)
    (await world.api.post("/users/me/fridge", {**body, "product_id": ids.tofu_off}, user=ids.me)).expect(
        409, message=INACTIVE_PRODUCT
    )
    (await world.api.post("/users/me/fridge", {**body, "product_id": ids.bare}, user=ids.me)).expect(
        409, message=NO_PRIMARY_INGREDIENT
    )
    (await world.api.post("/users/me/fridge", {**body, "product_id": ids.absent}, user=ids.me)).expect(404)
    (await world.api.post("/users/me/fridge", body)).expect(401)
    (await world.api.post("/users/me/fridge", {**body, "quantity": 0}, user=ids.me)).expect(422)


async def test_fridge_deactivated_item_stays_and_re_adding_is_duplicate(world: World) -> None:
    """담은 뒤 판매 중지된 상품은 냉장고에 그대로 보이고, 다시 담으면 "판매 중지" 가 아니라 "이미 담긴 상품" 입니다."""
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a)
    await world.conn.execute("UPDATE product SET is_active = FALSE WHERE product_id = $1", ids.kimchi_a)

    body = {"product_id": ids.kimchi_a, "quantity": 1, "unit": "개"}
    (await world.api.post("/users/me/fridge", body, user=ids.me)).expect(409, message=DUPLICATE_ITEM)
    items = (await world.api.get("/users/me/fridge", user=ids.me)).expect(200).data["items"]
    assert [item["product"]["product_id"] for item in items] == [ids.kimchi_a]


async def _waits_for_add_lock(conn: asyncpg.Connection, user_id: int, product_id: int) -> bool:
    """다른 커넥션에서 담기 잠금을 잡아 봅니다. 누가 쥐고 있으면 잠시 기다리다 포기하고 True 입니다."""
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '200ms'")
            await conn.execute(fridge_sql.LOCK_ITEM, user_id, product_id)
    except asyncpg.LockNotAvailableError:
        return True
    return False


async def test_fridge_add_is_serialized_per_user_and_product(world: World) -> None:
    """담기는 사용자·상품 잠금 아래에서 중복 확인과 삽입을 합니다 (PR #42 리뷰).

    잠금이 없으면 두 번 누른 요청이 둘 다 중복 확인을 통과하고, 그 사이 PRIMARY 재료가 바뀐 상품은 한 품목이
    두 줄이 됩니다. 잠금은 담은 트랜잭션이 끝날 때까지 쥐어지므로(여기서는 테스트 롤백까지), 다른 커넥션의
    같은 담기는 기다립니다. 다른 상품이나 다른 사용자의 담기는 기다리지 않습니다.
    """
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a)

    other = await connect()
    try:
        assert await _waits_for_add_lock(other, ids.me, ids.kimchi_a), "담기가 사용자·상품 잠금을 잡지 않았습니다"
        assert not await _waits_for_add_lock(other, ids.me, ids.neck_a)
        assert not await _waits_for_add_lock(other, ids.other, ids.kimchi_a)
    finally:
        await other.close()


async def test_fridge_mealkit_is_one_item(world: World) -> None:
    """PRIMARY 가 둘인 상품은 DB 에 두 행이지만 목록에서는 한 칸, 재료는 배열입니다. 삭제는 두 행을 다 지웁니다."""
    ids = world.ids
    (
        await world.api.post("/users/me/fridge", {"product_id": ids.mealkit, "quantity": 1, "unit": "팩"}, user=ids.me)
    ).expect(200)

    items = (await world.api.get("/users/me/fridge", user=ids.me)).expect(200).data["items"]
    assert len(items) == 1
    assert items[0]["product"] == {
        "product_id": ids.mealkit,
        "name": "찌개 밀키트",
        "storage_type": "냉장",
        "weight_g": None,
    }
    assert {i["name"] for i in items[0]["ingredients"]} == {"배추김치", "돼지고기"}

    (await world.api.delete(f"/users/me/fridge/{ids.mealkit}", user=ids.me)).expect(200)
    remaining = await world.conn.fetchval("SELECT count(*) FROM user_fridge WHERE user_id = $1", ids.me)
    assert remaining == 0


async def test_fridge_patch_changes_only_sent_fields(world: World) -> None:
    """보내지 않은 필드는 그대로 두고, expires_at 에 null 을 보내면 기한을 지웁니다."""
    ids = world.ids
    expires_at = in_days(7)
    (
        await world.api.post(
            "/users/me/fridge",
            {"product_id": ids.kimchi_a, "quantity": 500, "unit": "g", "expires_at": expires_at},
            user=ids.me,
        )
    ).expect(200)

    quantity_only = (
        (await world.api.patch(f"/users/me/fridge/{ids.kimchi_a}", {"quantity": 300}, user=ids.me)).expect(200).data
    )
    assert quantity_only["quantity"] == 300.0
    assert quantity_only["unit"] == "g"
    assert datetime.fromisoformat(quantity_only["expires_at"]) == datetime.fromisoformat(expires_at)

    cleared = (
        (await world.api.patch(f"/users/me/fridge/{ids.kimchi_a}", {"expires_at": None}, user=ids.me)).expect(200).data
    )
    assert cleared["expires_at"] is None
    assert cleared["quantity"] == 300.0

    (await world.api.patch(f"/users/me/fridge/{ids.kimchi_a}", {}, user=ids.me)).expect(422)


async def test_fridge_is_isolated_between_users(world: World) -> None:
    """다른 사용자의 품목은 보이지 않고, 고치거나 지우려 하면 존재 여부를 숨긴 404 입니다 (IDOR)."""
    ids = world.ids
    (
        await world.api.post(
            "/users/me/fridge", {"product_id": ids.kimchi_a, "quantity": 500, "unit": "g"}, user=ids.me
        )
    ).expect(200)

    assert (await world.api.get("/users/me/fridge", user=ids.other)).expect(200).data == {"items": []}
    (await world.api.patch(f"/users/me/fridge/{ids.kimchi_a}", {"quantity": 1}, user=ids.other)).expect(404)
    (await world.api.delete(f"/users/me/fridge/{ids.kimchi_a}", user=ids.other)).expect(404)

    mine = (await world.api.get("/users/me/fridge", user=ids.me)).expect(200).data["items"]
    assert [(item["product"]["product_id"], item["quantity"]) for item in mine] == [(ids.kimchi_a, 500.0)]


async def test_fridge_delete_then_gone(world: World) -> None:
    """삭제는 200 + data null 이고, 한 번 지운 품목은 수정·삭제 모두 404 입니다."""
    ids = world.ids
    await stock_fridge(world, ids.kimchi_a)

    deleted = (await world.api.delete(f"/users/me/fridge/{ids.kimchi_a}", user=ids.me)).expect(200)
    assert deleted.data is None
    (await world.api.delete(f"/users/me/fridge/{ids.kimchi_a}", user=ids.me)).expect(404)
    (await world.api.patch(f"/users/me/fridge/{ids.kimchi_a}", {"quantity": 1}, user=ids.me)).expect(404)
    assert (await world.api.get("/users/me/fridge", user=ids.me)).expect(200).data == {"items": []}


async def test_new_user_is_registered_on_first_write(world: World) -> None:
    """app_user 에 없는 사용자(JWT 의 BE users.id)도 냉장고 담기·찜·최근 본 기록이 됩니다.

    등록하지 않으면 냉장고 담기는 user_fridge 의 FK 위반으로 500, 찜·최근 본 기록은 "사용자를 찾을 수
    없습니다" 404 가 되어, BE 가 결제 뒤 사용자 JWT 로 냉장고를 채우는 계약(be-sync.md 3절)이 깨집니다.
    """
    ids = world.ids
    newcomer = ids.me + 777
    registered = "SELECT count(*) FROM app_user WHERE user_id = $1"
    assert await world.conn.fetchval(registered, newcomer) == 0

    body = {"product_id": ids.kimchi_a, "quantity": 1, "unit": "개"}
    (await world.api.post("/users/me/fridge", body, user=newcomer)).expect(200)
    (await world.api.post(f"/users/me/favorite-recipes/{ids.grill}", None, user=newcomer)).expect(200)
    (await world.api.post(f"/users/me/recent-recipes/{ids.stew}", None, user=newcomer)).expect(200)

    fridge = (await world.api.get("/users/me/fridge", user=newcomer)).expect(200).data["items"]
    assert [item["product"]["product_id"] for item in fridge] == [ids.kimchi_a]
    favorites = (await world.api.get("/users/me/favorite-recipes", user=newcomer)).expect(200).data["items"]
    assert [item["recipe_id"] for item in favorites] == [ids.grill]
    recents = (await world.api.get("/users/me/recent-recipes", user=newcomer)).expect(200).data["items"]
    assert [item["recipe_id"] for item in recents] == [ids.stew]
    assert await world.conn.fetchval(registered, newcomer) == 1


# --- RECENT-03 최근 본 레시피 선택 삭제 -----------------------------------------


async def test_recent_recipes_bulk_delete(world: World) -> None:
    """체크한 id 만 지우고, 없는 id 는 건너뛰며, 다른 사용자의 기록은 건드리지 않습니다."""
    ids = world.ids
    for recipe in (ids.stew, ids.grill, ids.tofu_dish):
        (await world.api.post(f"/users/me/recent-recipes/{recipe}", None, user=ids.me)).expect(200)
    (await world.api.post(f"/users/me/recent-recipes/{ids.stew}", None, user=ids.other)).expect(200)

    body = {"recipe_ids": [ids.stew, ids.grill, ids.absent, ids.stew]}
    deleted = (await world.api.delete("/users/me/recent-recipes", body, user=ids.me)).expect(200)
    assert deleted.data == {"deleted_count": 2}

    mine = (await world.api.get("/users/me/recent-recipes", user=ids.me)).expect(200).data["items"]
    assert [item["recipe_id"] for item in mine] == [ids.tofu_dish]
    others = (await world.api.get("/users/me/recent-recipes", user=ids.other)).expect(200).data["items"]
    assert [item["recipe_id"] for item in others] == [ids.stew]

    again = (await world.api.delete("/users/me/recent-recipes", body, user=ids.me)).expect(200)
    assert again.data == {"deleted_count": 0}


# --- HOME-01 / RECO-01 버블 --------------------------------------------------


async def test_bubbles_and_bubble_products_on_loaded_catalog(world: World) -> None:
    """버블은 적재된 레시피 전체가 입력이라 성질만 봅니다.

    켜진 버블의 상품은 한도를 지키고, 점수(버블 레시피 중 그 재료를 쓰는 수) 내림차순, 같은 점수면
    가격 오름차순입니다. 없는 버블은 404 입니다.
    """
    bubbles = (await world.api.get("/home/bubbles")).expect(200).data["items"]
    assert bubbles, "버블이 하나도 없습니다"
    for bubble in bubbles:
        assert set(bubble) == {"bubble_id", "label", "description", "enabled"}
        assert isinstance(bubble["enabled"], bool)

    enabled = [bubble for bubble in bubbles if bubble["enabled"]]
    if not enabled:
        pytest.skip("켜진 버블이 없습니다")
    for bubble in enabled[:2]:
        data = (
            (await world.api.get("/recommendations/products", bubble_id=bubble["bubble_id"], limit=5)).expect(200).data
        )
        assert data["bubble"] == {"id": bubble["bubble_id"], "label": bubble["label"]}
        assert len(data["items"]) <= 5
        ranking = [(-item["recommendation"]["score"], item["product"]["price"]) for item in data["items"]]
        assert ranking == sorted(ranking), "점수 내림차순, 같은 점수면 가격 오름차순이어야 합니다"

    (await world.api.get("/recommendations/products", bubble_id="NO_SUCH_BUBBLE")).expect(404)


# --- 실제 LLM (기본 skip, `--run-llm` 으로만) --------------------------------


@pytest.mark.llm
async def test_my_recipes_with_real_openrouter(world: World, caplog: pytest.LogCaptureFixture) -> None:
    """serving/.env 의 키로 OpenRouter 를 실제로 부릅니다. 앞 카드 중 한 장 이상은 LLM 문구여야 합니다.

    문구 내용은 매번 달라 단정하지 않습니다. 대체 건수는 서비스 로그로 셉니다. 과금이 있어 기본 skip 입니다.
    """
    from rag_lab.reason_service import OpenRouterReasonClient, ReasonSettings

    try:
        reason_settings = ReasonSettings.from_env(Settings().reason_environ())
    except RuntimeError:
        pytest.skip("OPENROUTER_API_KEY 가 없습니다")
    ids = world.ids
    rows = await world.conn.fetch("SELECT name FROM ingredient")
    await stock_fridge(world, ids.kimchi_a, ids.neck_a)

    async with httpx.AsyncClient() as http:
        vocabulary = frozenset(str(row["name"]) for row in rows)
        world.app.state.reason_runtime = ReasonRuntime.enabled_with(
            OpenRouterReasonClient(http, reason_settings), vocabulary
        )
        with caplog.at_level("INFO", logger="rag_lab.reason_service.service"):
            items = (await world.api.get("/recommendations/my-recipes", user=ids.me)).expect(200).data["items"]

    assert [item["recipe_id"] for item in items] == [ids.grill, ids.stew]
    summaries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("추천 이유 생성: 카드")]
    assert summaries, "추천 이유 생성 요약 로그가 없습니다"
    llm_cards = int(re.search(r"LLM (\d+)장", summaries[-1]).group(1))  # type: ignore[union-attr]
    assert llm_cards >= 1, summaries[-1]
