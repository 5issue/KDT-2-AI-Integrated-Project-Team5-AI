"""BE 내부 냉장고 upsert(`POST /internal/fridge/items`) 테스트. DB 도 네트워크도 쓰지 않습니다.

관리자 검사는 test_auth_jwt 와 같은 방식으로 RSA 키 쌍을 만들고 `PyJWKClient.fetch_data` 를
monkeypatch 해 JWKS 를 꾸밉니다. upsert 분기는 문장별로 정해진 결과를 내는 가짜 연결로 봅니다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jwt.algorithms import RSAAlgorithm

from serving import app_user_sql, fridge_sql
from serving.app import create_app
from serving.config import Settings
from serving.routers.internal import NO_PRIMARY_INGREDIENT, PRODUCT_NOT_FOUND

PATH = "/api/v1/internal/fridge/items"
JWKS_URL = "https://auth.internal/.well-known/jwks.json"
KID = "key-2026-09"
ADMIN_HEADER = {"X-User-Id": "9"}
BODY = {"user_id": 42, "items": [{"product_id": 101, "quantity": 2, "unit": "개"}]}


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _serve_jwks(monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey) -> None:
    public = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwks = {"keys": [{**public, "kid": KID, "use": "sig", "alg": "RS256"}]}
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: jwks)


def bearer(key: rsa.RSAPrivateKey, **extra: Any) -> dict[str, str]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {"sub": "9", "iat": now, "exp": now + timedelta(minutes=10), **extra}
    return {"Authorization": f"Bearer {jwt.encode(claims, key, algorithm='RS256', headers={'kid': KID})}"}


class _UpsertConnection:
    """upsert 가 부르는 문장에 정해진 결과를 냅니다. SQL 은 실행하지 않고 부른 순서를 ``calls`` 에 남깁니다.

    상품 101 은 처음 담는 상품, 202 는 이미 담긴 상품, 303 은 없는 상품, 404 는 재료 미연결 상품입니다.
    """

    EXISTING_PRODUCTS = {101, 202, 404}
    IN_FRIDGE = {202}

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_UpsertConnection]:
        yield self

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.calls.append(("begin", None))
        yield
        self.calls.append(("commit", None))

    async def execute(self, sql: str, *args: Any) -> str:
        if sql == app_user_sql.ENSURE_USER:
            self.calls.append(("user", args[0]))
            return "INSERT 0 1"
        assert sql == fridge_sql.LOCK_ITEM
        self.calls.append(("lock", args[1]))
        return "SELECT 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if sql == fridge_sql.PRODUCT_EXISTS:
            self.calls.append(("product", args[0]))
            return {"?column?": 1} if args[0] in self.EXISTING_PRODUCTS else None
        assert sql == fridge_sql.EXISTS_ITEM
        self.calls.append(("exists", args[1]))
        return {"?column?": 1} if args[1] in self.IN_FRIDGE else None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if sql == fridge_sql.ADD_QUANTITY:
            self.calls.append(("add", args[1]))
            # 기존 수량 3 에 새 수량을 더한 값. expires_at 은 플래그($6)가 참일 때만 새 값입니다.
            return [{"quantity": 3 + args[2], "unit": args[3], "expires_at": args[4] if args[5] else None}]
        assert sql == fridge_sql.INSERT_ITEM
        self.calls.append(("insert", args[1]))
        return [] if args[1] == 404 else [{"ingredient_id": 1}]


def _client(settings: Settings, pool: object) -> AsyncClient:
    app = create_app(settings)
    app.state.pool = pool
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"rate_limit_per_minute": 0, "rate_limit_reco_per_minute": 0}
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg]


@pytest.fixture
async def jwt_client() -> AsyncIterator[AsyncClient]:
    async with _client(_settings(jwt_jwks_url=JWKS_URL), _UpsertConnection()) as client:
        yield client


@pytest.fixture
async def header_client() -> AsyncIterator[AsyncClient]:
    async with _client(_settings(), _UpsertConnection()) as client:
        yield client


async def test_requires_token_in_jwt_mode(jwt_client: AsyncClient) -> None:
    """토큰이 없으면 401 이고, X-User-Id 만 보내도 무시되어 401 입니다."""
    for headers in ({}, ADMIN_HEADER):
        response = await jwt_client.post(PATH, headers=headers, json=BODY)
        assert response.status_code == 401, headers
        assert response.json()["error"] == "UNAUTHORIZED"


@pytest.mark.parametrize("extra", [{}, {"role": "USER"}, {"role": ["USER"]}, {"role": 1}, {"roles": "ADMIN"}])
async def test_non_admin_token_is_forbidden(
    jwt_client: AsyncClient, signing_key: rsa.RSAPrivateKey, extra: dict[str, Any]
) -> None:
    """서명은 맞지만 role 클레임이 ADMIN 이 아니면 403 FORBIDDEN 입니다. 클레임 이름이 다른 것도 거절합니다."""
    response = await jwt_client.post(PATH, headers=bearer(signing_key, **extra), json=BODY)

    assert response.status_code == 403, extra
    assert response.json()["error"] == "FORBIDDEN"


@pytest.mark.parametrize("extra", [{"role": "ADMIN"}, {"role": ["USER", "ADMIN"]}])
async def test_admin_token_passes(
    jwt_client: AsyncClient, signing_key: rsa.RSAPrivateKey, extra: dict[str, Any]
) -> None:
    """role 이 문자열 ADMIN 이거나 배열에 ADMIN 이 있으면 통과해 본문이 실행됩니다."""
    response = await jwt_client.post(PATH, headers=bearer(signing_key, **extra), json=BODY)

    assert response.status_code == 200, extra
    data = response.json()["data"]
    assert data["user_id"] == 42
    assert data["items"] == [
        {"product_id": 101, "action": "inserted", "quantity": 2.0, "unit": "개", "expires_at": None}
    ]


async def test_admin_role_claim_name_and_value_are_configurable(signing_key: rsa.RSAPrivateKey) -> None:
    """클레임 이름과 값은 설정으로 바꿉니다 (BE 규약이 다를 때)."""
    settings = _settings(jwt_jwks_url=JWKS_URL, jwt_admin_role_claim="authorities", jwt_admin_role="ROLE_ADMIN")
    async with _client(settings, _UpsertConnection()) as client:
        ok = await client.post(PATH, headers=bearer(signing_key, authorities=["ROLE_ADMIN"]), json=BODY)
        rejected = await client.post(PATH, headers=bearer(signing_key, role="ADMIN"), json=BODY)

    assert ok.status_code == 200
    assert rejected.status_code == 403


async def test_header_mode_requires_caller_id(header_client: AsyncClient) -> None:
    """X-User-Id 모드(로컬)에서는 호출자 id 헤더만 보고 role 검사는 없습니다."""
    missing = await header_client.post(PATH, json=BODY)
    assert missing.status_code == 401

    passed = await header_client.post(PATH, headers=ADMIN_HEADER, json=BODY)
    assert passed.status_code == 200


async def test_upsert_mixes_insert_update_and_skips() -> None:
    """새 상품은 담고, 있는 상품은 수량을 더하고, 없는 상품·재료 미연결 상품은 skipped 로 냅니다."""
    conn = _UpsertConnection()
    body = {
        "user_id": 42,
        "items": [
            {"product_id": 404, "quantity": 1, "unit": "팩"},
            {"product_id": 202, "quantity": 2, "unit": "g", "expires_at": "2026-10-10T00:00:00Z"},
            {"product_id": 303, "quantity": 1, "unit": "개"},
            {"product_id": 101, "quantity": 5, "unit": "개"},
        ],
    }
    async with _client(_settings(), conn) as client:
        response = await client.post(PATH, headers=ADMIN_HEADER, json=body)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == [
        {"product_id": 101, "action": "inserted", "quantity": 5.0, "unit": "개", "expires_at": None},
        {"product_id": 202, "action": "updated", "quantity": 5.0, "unit": "g", "expires_at": "2026-10-10T00:00:00Z"},
    ]
    assert data["skipped"] == [
        {"product_id": 303, "reason": PRODUCT_NOT_FOUND},
        {"product_id": 404, "reason": NO_PRIMARY_INGREDIENT},
    ]
    # 한 트랜잭션, 대상 사용자 등록이 먼저, 상품 id 순으로 처리, 없는 상품은 잠금을 잡지 않습니다.
    assert conn.calls[:2] == [("begin", None), ("user", 42)]
    assert conn.calls[-1] == ("commit", None)
    assert [c for c in conn.calls if c[0] == "lock"] == [("lock", 101), ("lock", 202), ("lock", 404)]
    checked = [c[1] for c in conn.calls if c[0] == "product"]
    assert checked == [101, 202, 303, 404]
    assert ("add", 202) in conn.calls and ("insert", 101) in conn.calls and ("insert", 404) in conn.calls


async def test_update_keeps_expires_at_when_not_sent() -> None:
    """expires_at 을 보내지 않으면 기존 기한을 유지합니다 (null 을 보내면 기한 없음으로 바꿉니다)."""
    conn = _UpsertConnection()
    body = {"user_id": 42, "items": [{"product_id": 202, "quantity": 1, "unit": "g"}]}
    async with _client(_settings(), conn) as client:
        response = await client.post(PATH, headers=ADMIN_HEADER, json=body)

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["expires_at"] is None


async def test_body_is_validated(header_client: AsyncClient) -> None:
    """빈 items, 같은 상품 중복, 수량 0, user_id 누락은 422 INVALID_INPUT_VALUE 입니다."""
    bad_bodies = [
        {"user_id": 42, "items": []},
        {"user_id": 42, "items": [{"product_id": 1, "quantity": 1, "unit": "개"}] * 2},
        {"user_id": 42, "items": [{"product_id": 1, "quantity": 0, "unit": "개"}]},
        {"items": [{"product_id": 1, "quantity": 1, "unit": "개"}]},
        {"user_id": 0, "items": [{"product_id": 1, "quantity": 1, "unit": "개"}]},
    ]
    for body in bad_bodies:
        response = await header_client.post(PATH, headers=ADMIN_HEADER, json=body)
        assert response.status_code == 422, body
        assert response.json()["error"] == "INVALID_INPUT_VALUE", body
