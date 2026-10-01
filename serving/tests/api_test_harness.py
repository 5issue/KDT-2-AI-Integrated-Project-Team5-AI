"""serving HTTP 통합 테스트의 도우미. 테스트 파일이 아니라 pytest 가 수집하지 않습니다.

세 가지를 모아 둡니다.

- **시드**: 테스트 전용 데이터(9,300,000,000 대역). recsys_sql(9,100M), 데모(9,200M) 대역과 겹치지 않습니다.
  어느 DB 에서나 같은 모양인 컬럼만 씁니다. 비어도 되는 `product.image_url` 은 넣지 않고, `storage_guideline`
  은 DB 마다 제약이 달라(원천에서 옮긴 표와 0013 이 만든 표) 시드하지 않습니다.
- **롤백 세계**: 트랜잭션 하나를 열고 그 커넥션을 앱의 풀 자리에 끼웁니다. 앱의 모든 요청이 그 안에서 돌고,
  끝나면 무조건 롤백해 공용 DB 에 아무것도 남지 않습니다(recsys_sql DB 테스트와 같은 방식).
- **supertest 식 클라이언트**: `(await api.get(...)).expect(200).data`. `expect` 가 상태 코드와 모든
  `/api/v1` 응답이 지켜야 하는 envelope 계약을 함께 봅니다.

앱은 lifespan 을 타지 않습니다. 그래서 추천 이유는 규칙 문구로 고정되고, rate limit 은 끕니다.
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
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.config import Settings
from serving.db import normalize_neon_dsn

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
    """시드 식별자."""

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


def seed_sql(ids: Ids) -> str:
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


class TransactionPool:
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

    async def delete(self, path: str, body: Any = None, *, user: int | None = None) -> Reply:
        return await self.request("DELETE", path, user=user, json=body)


@dataclass(slots=True)
class World:
    """테스트 하나의 세계. 롤백되는 커넥션, 그 위에서 도는 앱, 시드 식별자."""

    api: Api
    app: FastAPI
    conn: asyncpg.Connection
    ids: Ids


async def connect() -> asyncpg.Connection:
    """serving 설정의 DB 에 커넥션 하나를 엽니다. 닫는 것은 부른 쪽 몫입니다.

    DB 주소는 serving 설정(`serving/.env` 또는 환경변수 `DATABASE_URL`)에서 읽습니다.
    """
    settings = Settings()
    dsn, connect_kwargs = normalize_neon_dsn(settings.require_database_url())
    return await asyncpg.connect(dsn, command_timeout=settings.db_command_timeout, **connect_kwargs)


@asynccontextmanager
async def open_world() -> AsyncIterator[World]:
    """시드를 넣은 트랜잭션 위에 앱을 띄웁니다. 빠져나오면 전부 롤백합니다."""
    conn = await connect()
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute(seed_sql(IDS))
        app = create_app(
            Settings(_env_file=None, rate_limit_per_minute=0, rate_limit_reco_per_minute=0)  # type: ignore[call-arg]
        )
        app.state.pool = TransactionPool(conn)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield World(api=Api(client), app=app, conn=conn, ids=IDS)
    finally:
        await transaction.rollback()
        await conn.close()


def in_days(days: int) -> str:
    """지금부터 ``days`` 일 뒤(음수면 전)의 UTC 시각. API 가 받는 형식입니다."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def stock_fridge(world: World, *products: int) -> None:
    """내 냉장고에 상품을 API 로 담습니다. 담기 경로 자체도 함께 지나갑니다."""
    for product_id in products:
        body = {"product_id": product_id, "quantity": 1, "unit": "개"}
        (await world.api.post("/users/me/fridge", body, user=world.ids.me)).expect(200)
