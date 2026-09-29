"""Bearer JWT 인증 테스트. DB 도 네트워크도 쓰지 않습니다.

JWKS 는 테스트가 만든 RSA 키 쌍으로 꾸미고, `PyJWKClient.fetch_data` 를 monkeypatch 해서
auth 서버 대신 그 JWKS 를 돌려줍니다. 검증 통과는 "DB 미연결 503" 으로, 실패는 401 로 봅니다
(FastAPI 는 의존성을 먼저 풀어 인증이 통과해야 풀을 찾습니다).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from serving.app import create_app
from serving.auth import bearer_token
from serving.config import Settings

JWKS_URL = "https://auth.internal/.well-known/jwks.json"
ISSUER = "https://auth.internal"
KID = "key-2026-09"
FRIDGE = "/api/v1/users/me/fridge"
MISSING = "/api/v1/recipes/1/missing-products"


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(signing_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    """auth 서버가 내는 JWKS 모양. 공개키 하나에 kid 를 붙입니다."""
    public = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": KID, "use": "sig", "alg": "RS256"}]}


@pytest.fixture(autouse=True)
def _serve_jwks(monkeypatch: pytest.MonkeyPatch, jwks: dict[str, Any]) -> None:
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: jwks)


def make_token(
    key: rsa.RSAPrivateKey,
    *,
    sub: object = "42",
    issuer: str | None = ISSUER,
    expires_in: timedelta = timedelta(minutes=10),
    kid: str | None = KID,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {"sub": sub, "iat": now, "exp": now + expires_in}
    if issuer is not None:
        claims["iss"] = issuer
    claims.update(extra or {})
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def jwt_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "jwt_jwks_url": JWKS_URL,
        "jwt_issuer": ISSUER,
        "rate_limit_per_minute": 0,
        "rate_limit_reco_per_minute": 0,
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg]


def jwt_client(settings: Settings | None = None, *, pool: object | None = None) -> AsyncClient:
    """JWT 가 켜진 앱의 클라이언트.

    FastAPI 는 의존성을 선언 순서로 풀어 풀(503)이 인증(401)보다 먼저입니다. 401 을 보려면
    풀 자리에 자리표시자(``object()``)를 두고, 인증 통과를 보려면 ``None`` 으로 503 을 냅니다.
    """
    app = create_app(settings or jwt_settings())
    app.state.pool = pool
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def passing() -> AsyncIterator[AsyncClient]:
    """인증이 통과하면 DB 미연결 503 이 나오는 클라이언트."""
    async with jwt_client(pool=None) as client:
        yield client


@pytest.fixture
async def rejecting() -> AsyncIterator[AsyncClient]:
    """인증 실패(401)를 보기 위한 클라이언트. 풀 자리표시자는 쓰이지 않습니다."""
    async with jwt_client(pool=object()) as client:
        yield client


async def test_valid_token_passes_auth(passing: AsyncClient, signing_key: rsa.RSAPrivateKey) -> None:
    """서명이 맞으면 인증을 지나 DB 미연결 503 까지 갑니다."""
    response = await passing.get(FRIDGE, headers=bearer(make_token(signing_key)))
    assert response.status_code == 503
    assert response.json()["error"] == "SERVICE_UNAVAILABLE"


async def test_missing_bearer_is_401(rejecting: AsyncClient) -> None:
    response = await rejecting.get(FRIDGE)
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


async def test_x_user_id_is_ignored_when_jwt_enabled(rejecting: AsyncClient) -> None:
    """JWT 모드에서 헤더 한 줄로 우회할 수 없습니다."""
    response = await rejecting.get(FRIDGE, headers={"X-User-Id": "42"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "case",
    ["wrong_key", "expired", "wrong_issuer", "no_issuer", "unknown_kid", "non_numeric_sub", "zero_sub", "garbage"],
)
async def test_invalid_tokens_are_401(
    rejecting: AsyncClient, signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey, case: str
) -> None:
    """실패 사유가 무엇이든 응답은 401 하나입니다 (사유 구분은 로그에만)."""
    tokens = {
        "wrong_key": lambda: make_token(other_key),
        "expired": lambda: make_token(signing_key, expires_in=timedelta(minutes=-5)),
        "wrong_issuer": lambda: make_token(signing_key, issuer="https://evil.example"),
        "no_issuer": lambda: make_token(signing_key, issuer=None),
        "unknown_kid": lambda: make_token(signing_key, kid="rotated-away"),
        "non_numeric_sub": lambda: make_token(signing_key, sub="user-42"),
        "zero_sub": lambda: make_token(signing_key, sub="0"),
        "garbage": lambda: "not.a.jwt",
    }
    response = await rejecting.get(FRIDGE, headers=bearer(tokens[case]()))
    assert response.status_code == 401, case
    assert response.json()["error"] == "UNAUTHORIZED"


async def test_non_bearer_scheme_is_401(rejecting: AsyncClient, signing_key: rsa.RSAPrivateKey) -> None:
    response = await rejecting.get(FRIDGE, headers={"Authorization": f"Basic {make_token(signing_key)}"})
    assert response.status_code == 401


async def test_optional_route_allows_anonymous_but_rejects_bad_token(
    passing: AsyncClient, rejecting: AsyncClient, other_key: rsa.RSAPrivateKey
) -> None:
    """비로그인 허용 경로: 토큰이 없으면 익명(503 까지 감), 있는데 틀리면 401."""
    anonymous = await passing.get(MISSING)
    assert anonymous.status_code == 503
    forged = await rejecting.get(MISSING, headers=bearer(make_token(other_key)))
    assert forged.status_code == 401


async def test_audience_is_checked_only_when_configured(signing_key: rsa.RSAPrivateKey) -> None:
    settings = jwt_settings(jwt_audience="ai-serving")
    async with jwt_client(settings, pool=object()) as client:
        without = await client.get(FRIDGE, headers=bearer(make_token(signing_key)))
        assert without.status_code == 401
        wrong = await client.get(FRIDGE, headers=bearer(make_token(signing_key, extra={"aud": "other"})))
        assert wrong.status_code == 401
    async with jwt_client(settings, pool=None) as client:
        right = await client.get(FRIDGE, headers=bearer(make_token(signing_key, extra={"aud": "ai-serving"})))
        assert right.status_code == 503


async def test_jwks_unreachable_is_503(
    monkeypatch: pytest.MonkeyPatch, rejecting: AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    """auth 서버에 닿지 못하면 위조가 아니라 장애라 503 입니다 (풀 자리표시자라 DB 503 과 구분됩니다)."""

    def fail(self: jwt.PyJWKClient) -> dict[str, Any]:
        raise jwt.exceptions.PyJWKClientConnectionError("down")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
    response = await rejecting.get(FRIDGE, headers=bearer(make_token(signing_key)))
    assert response.status_code == 503
    assert response.json()["error"] == "SERVICE_UNAVAILABLE"
    assert response.json()["message"] == "인증 서버에 연결할 수 없습니다."


async def test_rate_limit_keys_by_token_sub(signing_key: rsa.RSAPrivateKey) -> None:
    """rate limit 키는 토큰의 sub 입니다. 다른 사용자는 서로 한도를 나누지 않습니다."""
    async with jwt_client(jwt_settings(rate_limit_per_minute=1), pool=None) as client:
        first = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="1")))
        assert first.status_code == 503
        again = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="1")))
        assert again.status_code == 429
        other = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="2")))
        assert other.status_code == 503


def test_header_mode_when_jwks_url_empty() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    assert app.state.jwt_verifier is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Bearer   abc  ", "abc"),
        ("Bearer ", None),
        ("Basic abc", None),
        (None, None),
    ],
)
def test_bearer_token_parsing(header: str | None, expected: str | None) -> None:
    assert bearer_token(header) == expected
