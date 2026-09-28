"""serving HTTP 통합 테스트. 실제 PostgreSQL 위에서 요청부터 응답까지 한 번에 봅니다.

supertest 처럼 앱 전체(미들웨어 -> 라우터 -> 카탈로그 SQL -> 응답 스키마)를 HTTP 로 부르고
상태 코드, envelope, 본문을 이어서 확인합니다. 단위 테스트가 가짜 풀로 흉내 내는 판정을
여기서는 실제 스키마와 SQL 이 합니다.

- **테스트마다 트랜잭션 하나를 열고 끝나면 무조건 롤백합니다.** 앱의 모든 요청이 그 커넥션
  위에서 돌아 공용 dev 브랜치에 아무것도 남지 않습니다(recsys_sql DB 테스트와 같은 방식).
- **데이터는 테스트가 직접 넣습니다**(9,300,000,000 대역). 적재된 카탈로그에 기대지 않아 결과를
  정확히 단정할 수 있습니다. 버블과 보관 가이드처럼 적재 데이터가 입력인 API 만 성질을 봅니다.
- 시드는 마이그레이션 0011~0015 어디서나 있는 컬럼만 씁니다. dev 브랜치가 0011 이라
  `product.image_url`(0015)과 `storage_guideline` 시드(0013 과 컬럼이 다름)는 쓰지 않습니다.
- 추천 이유는 규칙 문구로 고정됩니다(lifespan 을 타지 않아 LLM 클라이언트가 없음). 실제
  OpenRouter 경로는 맨 아래 `llm` 테스트 하나가 보며, `-m llm` 으로만 돕니다.
- rate limit 은 끕니다. 한도 동작은 `test_rate_limit.py` 가 봅니다.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings
from serving.db import normalize_neon_dsn
from serving.routers.fridge import DUPLICATE_ITEM, INACTIVE_PRODUCT, NO_PRIMARY_INGREDIENT

pytestmark = pytest.mark.db

BASE = 9_300_000_000
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# 상태 코드별로 envelope 의 error 에 실려야 하는 값 (명세 에러표).
ERROR_CODES = {
    401: "UNAUTHORIZED",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    422: "INVALID_INPUT_VALUE",
    503: "SERVICE_UNAVAILABLE",
}


@dataclass(frozen=True, slots=True)
class Ids:
    """시드 식별자. 실제 데이터와 recsys_sql(9,100M), 데모(9,200M) 대역과 겹치지 않습니다."""

    me: int = BASE + 1
    other: int = BASE + 2

    kimchi: int = BASE + 11
    pork: int = BASE + 12
    pork_neck: int = BASE + 13  # 돼지고기의 자식. 목심 상품으로 돼지고기 레시피를 채웁니다
    tofu: int = BASE + 14
    salt: int = BASE + 15  # 상비재료
    green_onion: int = BASE + 16
    sesame_oil: int = BASE + 17  # 파는 상품이 없습니다

    kimchi_a: int = BASE + 21
    neck_a: int = BASE + 22
    tofu_a: int = BASE + 23  # 3000원, 냉장
    tofu_b: int = BASE + 24  # 2500원
    tofu_off: int = BASE + 25  # 판매 중지, 1000원
    onion_a: int = BASE + 26
    onion_soldout: int = BASE + 27  # 재고 0
    mealkit: int = BASE + 28  # PRIMARY 가 김치·돼지고기 둘
    bare: int = BASE + 29  # 재료 미연결

    stew: int = BASE + 31  # 필수 김치·돼지고기·두부, 20분
    grill: int = BASE + 32  # 필수 돼지고기·소금(상비), 30분
    tofu_dish: int = BASE + 33  # 필수 두부·대파·참기름, 10분
    far: int = BASE + 34  # 필수 김치·두부·대파·참기름, 40분

    absent: int = BASE + 999  # 어디에도 없는 id


IDS = Ids()


def _seed_sql(ids: Ids) -> str:
    """시드 전체를 한 번에 보내는 SQL. 왕복 한 번으로 끝냅니다. 값은 전부 테스트 상수입니다."""
    ingredients = [
        (ids.kimchi, "배추김치", False, None),
        (ids.pork, "돼지고기", False, None),
        (ids.pork_neck, "목심", False, ids.pork),
        (ids.tofu, "두부", False, None),
        (ids.salt, "소금", True, None),
        (ids.green_onion, "대파", False, None),
        (ids.sesame_oil, "참기름", False, None),
    ]
    # (id, 이름, 가격, 재고, 판매 여부, 보관 장소)
    products = [
        (ids.kimchi_a, "김치A", 4000, 10, True, "냉장"),
        (ids.neck_a, "목심A", 9000, 10, True, "냉장"),
        (ids.tofu_a, "두부A", 3000, 10, True, "냉장"),
        (ids.tofu_b, "두부B", 2500, 10, True, "냉장"),
        (ids.tofu_off, "두부C", 1000, 10, False, "냉장"),
        (ids.onion_a, "대파A", 2000, 10, True, None),
        (ids.onion_soldout, "대파B", 1500, 0, True, None),
        (ids.mealkit, "찌개 밀키트", 15000, 10, True, "냉장"),
        (ids.bare, "재료 미연결 상품", 5000, 10, True, None),
    ]
    primaries = [
        (ids.kimchi_a, ids.kimchi),
        (ids.neck_a, ids.pork_neck),
        (ids.tofu_a, ids.tofu),
        (ids.tofu_b, ids.tofu),
        (ids.tofu_off, ids.tofu),
        (ids.onion_a, ids.green_onion),
        (ids.onion_soldout, ids.green_onion),
        (ids.mealkit, ids.kimchi),
        (ids.mealkit, ids.pork),
    ]
    recipes = [
        (ids.stew, "시드 김치찌개", 20),
        (ids.grill, "시드 삼겹살구이", 30),
        (ids.tofu_dish, "시드 두부부침", 10),
        (ids.far, "시드 먼 요리", 40),
    ]
    # 전부 필수 재료입니다. 선택 재료는 매칭률에 들어가지 않는다는 규칙은 recsys_sql 이 봅니다.
    lines = [
        (ids.stew, ids.kimchi),
        (ids.stew, ids.pork),
        (ids.stew, ids.tofu),
        (ids.grill, ids.pork),
        (ids.grill, ids.salt),
        (ids.tofu_dish, ids.tofu),
        (ids.tofu_dish, ids.green_onion),
        (ids.tofu_dish, ids.sesame_oil),
        (ids.far, ids.kimchi),
        (ids.far, ids.tofu),
        (ids.far, ids.green_onion),
        (ids.far, ids.sesame_oil),
    ]

    def sql_text(value: str | None) -> str:
        return "NULL" if value is None else "'" + value.replace("'", "''") + "'"

    ingredient_rows = ",\n".join(
        f"({i}, {sql_text(n)}, {sql_text(n)}, TRUE, '{{}}', '{{}}', {str(p).upper()}, "
        f"{sql_text('IT-SEED:' + n)}, {'NULL' if parent is None else parent})"
        for i, n, p, parent in ingredients
    )
    product_rows = ",\n".join(
        f"({i}, {sql_text(f'IT-{i}')}, {sql_text(n)}, 'INGREDIENT', {price}, {stock}, {str(active).upper()}, "
        f"{sql_text(storage)}, 'IT-SEED', {sql_text(str(i))})"
        for i, n, price, stock, active, storage in products
    )
    primary_rows = ",\n".join(f"({product}, {ingredient}, 'PRIMARY')" for product, ingredient in primaries)
    recipe_rows = ",\n".join(
        f"({i}, {sql_text(n)}, 'EASY', {cook}, 2, 'IT-SEED', {sql_text(str(i))})" for i, n, cook in recipes
    )
    line_rows = ",\n".join(f"({recipe}, {ingredient}, 100, 'g', TRUE)" for recipe, ingredient in lines)
    return f"""
INSERT INTO app_user (user_id) VALUES ({ids.me}), ({ids.other});
INSERT INTO ingredient (
    ingredient_id, name, normalized_name, is_raw_material, aliases, nutrition, is_pantry,
    source_identity_key, parent_ingredient_id
) VALUES
{ingredient_rows};
INSERT INTO product (
    product_id, sku, name, product_type, price, stock_quantity, is_active, storage_type,
    source_type, source_product_id
) VALUES
{product_rows};
INSERT INTO product_ingredient (product_id, ingredient_id, role) VALUES
{primary_rows};
INSERT INTO recipe (recipe_id, name, difficulty, cook_time_min, servings, source_type, source_recipe_id) VALUES
{recipe_rows};
INSERT INTO recipe_ingredient (recipe_id, ingredient_id, quantity, unit, is_required) VALUES
{line_rows};
INSERT INTO recipe_step (recipe_id, step_no, instruction) VALUES
({ids.stew}, 2, '김치와 두부를 넣고 끓입니다.'),
({ids.stew}, 1, '돼지고기를 볶습니다.');
"""


class _TransactionPool:
    """앱의 풀 자리에 끼우는 커넥션 하나. 모든 요청이 테스트 트랜잭션 안에서 돕니다."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


@dataclass(slots=True)
class Reply:
    """응답 하나. ``expect`` 로 상태와 envelope 계약을 확인하고 ``data`` 로 본문을 꺼냅니다."""

    response: httpx.Response

    def expect(self, status: int, *, message: str | None = None) -> Reply:
        """상태 코드와 모든 /api/v1 응답이 지켜야 하는 계약을 한 번에 봅니다.

        실패하면 요청과 응답 본문을 함께 보여 줍니다.
        """
        response = self.response
        where = f"{response.request.method} {response.request.url} -> {response.status_code} {response.text[:400]}"
        assert response.status_code == status, where
        body = response.json()
        assert set(body) == {"status", "message", "data", "error", "timestamp"}, where
        assert TIMESTAMP.match(body["timestamp"]), where
        assert response.headers.get("X-Request-Id"), where
        if status < 400:
            assert body["status"] == "SUCCESS" and body["error"] is None, where
        else:
            assert body["status"] == "ERROR" and body["data"] is None, where
            assert body["error"] == ERROR_CODES[status], where
        if message is not None:
            assert body["message"] == message, where
        return self

    @property
    def data(self) -> Any:
        return self.response.json()["data"]


class Api:
    """/api/v1 클라이언트. ``user`` 를 주면 X-User-Id 로 싣습니다."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def request(
        self,
        method: str,
        path: str,
        *,
        user: int | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Reply:
        headers = {} if user is None else {"X-User-Id": str(user)}
        response = await self._client.request(method, f"/api/v1{path}", headers=headers, json=json, params=params)
        return Reply(response)

    async def get(self, path: str, *, user: int | None = None, **params: Any) -> Reply:
        return await self.request("GET", path, user=user, params=params)

    async def post(self, path: str, body: Any, *, user: int | None = None) -> Reply:
        return await self.request("POST", path, user=user, json=body)

    async def patch(self, path: str, body: Any, *, user: int | None = None) -> Reply:
        return await self.request("PATCH", path, user=user, json=body)

    async def delete(self, path: str, *, user: int | None = None) -> Reply:
        return await self.request("DELETE", path, user=user)


@dataclass(slots=True)
class World:
    """테스트 하나의 세계. 롤백되는 커넥션, 그 위에서 도는 앱, 시드 식별자."""

    api: Api
    app: FastAPI
    conn: asyncpg.Connection
    ids: Ids


@pytest.fixture
async def world() -> AsyncIterator[World]:
    """시드를 넣은 트랜잭션 위에 앱을 띄웁니다. 테스트가 끝나면 전부 롤백합니다."""
    settings = Settings()
    dsn, connect_kwargs = normalize_neon_dsn(settings.require_database_url())
    conn = await asyncpg.connect(dsn, command_timeout=settings.db_command_timeout, **connect_kwargs)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute(_seed_sql(IDS))
        app = create_app(
            Settings(_env_file=None, rate_limit_per_minute=0, rate_limit_reco_per_minute=0)  # type: ignore[call-arg]
        )
        app.state.pool = _TransactionPool(conn)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield World(api=Api(client), app=app, conn=conn, ids=IDS)
    finally:
        await transaction.rollback()
        await conn.close()


def _in_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _stock_fridge(world: World, *products: int) -> None:
    """내 냉장고에 상품을 API 로 담습니다. 담기 경로 자체도 함께 지나갑니다."""
    for product_id in products:
        body = {"product_id": product_id, "quantity": 1, "unit": "개"}
        (await world.api.post("/users/me/fridge", body, user=world.ids.me)).expect(200)


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
    await _stock_fridge(world, ids.kimchi_a, ids.neck_a)

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
    await _stock_fridge(world, ids.kimchi_a, ids.neck_a)
    await world.api.post(
        "/users/me/fridge",
        {"product_id": ids.tofu_a, "quantity": 1, "unit": "모", "expires_at": _in_days(-1)},
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
    await _stock_fridge(world, ids.kimchi_a, ids.neck_a)

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
    await _stock_fridge(world, ids.kimchi_a, ids.neck_a)

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
    await _stock_fridge(world, ids.kimchi_a, ids.neck_a)

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
    expires_at = _in_days(7)
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
    await _stock_fridge(world, ids.kimchi_a)

    deleted = (await world.api.delete(f"/users/me/fridge/{ids.kimchi_a}", user=ids.me)).expect(200)
    assert deleted.data is None
    (await world.api.delete(f"/users/me/fridge/{ids.kimchi_a}", user=ids.me)).expect(404)
    (await world.api.patch(f"/users/me/fridge/{ids.kimchi_a}", {"quantity": 1}, user=ids.me)).expect(404)
    assert (await world.api.get("/users/me/fridge", user=ids.me)).expect(200).data == {"items": []}


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


# --- 실제 LLM (기본 skip, `-m llm` 으로만) -----------------------------------


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
    await _stock_fridge(world, ids.kimchi_a, ids.neck_a)

    async with httpx.AsyncClient() as http:
        world.app.state.reason_client = OpenRouterReasonClient(http, reason_settings)
        world.app.state.ingredient_vocabulary = frozenset(str(row["name"]) for row in rows)
        with caplog.at_level("INFO", logger="rag_lab.reason_service.service"):
            items = (await world.api.get("/recommendations/my-recipes", user=ids.me)).expect(200).data["items"]

    assert [item["recipe_id"] for item in items] == [ids.grill, ids.stew]
    summaries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("추천 이유 생성: 카드")]
    assert summaries, "추천 이유 생성 요약 로그가 없습니다"
    llm_cards = int(re.search(r"LLM (\d+)장", summaries[-1]).group(1))  # type: ignore[union-attr]
    assert llm_cards >= 1, summaries[-1]
