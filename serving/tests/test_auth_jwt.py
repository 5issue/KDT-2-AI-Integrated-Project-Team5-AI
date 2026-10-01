"""Bearer JWT 인증 테스트. DB 도 네트워크도 쓰지 않습니다.

JWKS 는 테스트가 만든 RSA 키 쌍으로 꾸미고, `PyJWKClient.fetch_data` 를 monkeypatch 해서
auth 서버 대신 그 JWKS 를 돌려줍니다.

FastAPI 는 의존성을 선언 순서로 풀어 풀(`PoolDep`)이 인증보다 먼저입니다. 그래서
- 인증 **실패**(401)는 풀 자리표시자(`object()`)를 둔 앱에서 봅니다(풀은 쓰이지 않음).
- 인증 **통과**는 빈 결과를 돌려주는 가짜 풀로 엔드포인트 본문까지 실행시켜 200/404 로 봅니다.
  풀을 `None` 으로 두면 503 이 인증보다 먼저 나와 아무것도 입증하지 못합니다(CodeRabbit PR #45).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWKClientConnectionError

from serving.app import create_app
from serving.auth import JwtVerifier, _parse_user_id, bearer_token
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


def jwks_for(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, Any]:
    """auth 서버가 내는 JWKS 모양. 공개키 하나에 kid 를 붙입니다."""
    public = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": kid, "use": "sig", "alg": "RS256"}]}


@pytest.fixture(scope="module")
def jwks(signing_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    return jwks_for(signing_key)


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


class _EmptyPool:
    """뭘 물어도 없다고 답하는 풀. 인증을 지난 요청이 본문까지 실행되게 합니다."""

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_EmptyPool]:
        yield self

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        return None


def jwt_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "jwt_jwks_url": JWKS_URL,
        "jwt_issuer": ISSUER,
        "rate_limit_per_minute": 0,
        "rate_limit_reco_per_minute": 0,
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg]


def jwt_client(settings: Settings | None = None, *, pool: object | None = None) -> AsyncClient:
    """JWT 가 켜진 앱의 클라이언트. `pool` 은 모듈 docstring 대로 고릅니다."""
    app = create_app(settings or jwt_settings())
    app.state.pool = pool
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def passing() -> AsyncIterator[AsyncClient]:
    """인증이 통과하면 본문까지 실행되는 클라이언트 (냉장고 GET 은 빈 목록 200, 레시피는 404)."""
    async with jwt_client(pool=_EmptyPool()) as client:
        yield client


@pytest.fixture
async def rejecting() -> AsyncIterator[AsyncClient]:
    """인증 실패(401)를 보기 위한 클라이언트. 풀 자리표시자는 쓰이지 않습니다."""
    async with jwt_client(pool=object()) as client:
        yield client


async def test_valid_token_passes_auth(passing: AsyncClient, signing_key: rsa.RSAPrivateKey) -> None:
    """서명이 맞으면 인증을 지나 엔드포인트 본문이 실행됩니다."""
    response = await passing.get(FRIDGE, headers=bearer(make_token(signing_key)))
    assert response.status_code == 200
    assert response.json()["data"] == {"items": []}


def test_verifier_returns_sub(signing_key: rsa.RSAPrivateKey) -> None:
    assert JwtVerifier(jwt_settings()).user_id(make_token(signing_key, sub="42")) == 42


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
    passing: AsyncClient, rejecting: AsyncClient, signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
) -> None:
    """비로그인 허용 경로: 토큰이 없으면 익명으로 본문 실행(빈 풀이라 404), 있으면 검증합니다."""
    anonymous = await passing.get(MISSING)
    assert anonymous.status_code == 404
    signed_in = await passing.get(MISSING, headers=bearer(make_token(signing_key)))
    assert signed_in.status_code == 404
    forged = await rejecting.get(MISSING, headers=bearer(make_token(other_key)))
    assert forged.status_code == 401


async def test_audience_is_checked_only_when_configured(signing_key: rsa.RSAPrivateKey) -> None:
    settings = jwt_settings(jwt_audience="ai-serving")
    async with jwt_client(settings, pool=object()) as client:
        without = await client.get(FRIDGE, headers=bearer(make_token(signing_key)))
        assert without.status_code == 401
        wrong = await client.get(FRIDGE, headers=bearer(make_token(signing_key, extra={"aud": "other"})))
        assert wrong.status_code == 401
    async with jwt_client(settings, pool=_EmptyPool()) as client:
        right = await client.get(FRIDGE, headers=bearer(make_token(signing_key, extra={"aud": "ai-serving"})))
        assert right.status_code == 200


async def test_jwks_unreachable_is_503(
    monkeypatch: pytest.MonkeyPatch, rejecting: AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    """auth 서버에 닿지 못하면 위조가 아니라 장애라 503 입니다 (풀 자리표시자라 DB 503 과 구분됩니다)."""

    def fail(self: jwt.PyJWKClient) -> dict[str, Any]:
        raise PyJWKClientConnectionError("down")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
    response = await rejecting.get(FRIDGE, headers=bearer(make_token(signing_key)))
    assert response.status_code == 503
    assert response.json()["error"] == "SERVICE_UNAVAILABLE"
    assert response.json()["message"] == "인증 서버에 연결할 수 없습니다."


def test_removed_key_is_rejected_after_jwks_cache_expires(
    monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
) -> None:
    """JWKS 에서 뺀 키는 캐시 수명이 지나면 더는 통하지 않습니다 (키별 LRU 캐시를 끈 이유)."""
    verifier = JwtVerifier(jwt_settings(jwt_jwks_cache_seconds=300))
    token = make_token(signing_key)
    assert verifier.user_id(token) == 42

    # auth 서버가 키를 교체했고, JWK Set 캐시가 만료된 상황
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: jwks_for(other_key, kid="key-2026-10"))
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 301)
    with pytest.raises(HTTPException) as excinfo:
        verifier.user_id(token)
    assert excinfo.value.status_code == 401


async def test_one_request_verifies_the_token_once(
    monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey
) -> None:
    """액세스 로그·rate limit 미들웨어와 의존성이 한 요청에서 서명을 한 번만 검증합니다."""
    calls = 0
    verify = JwtVerifier.user_id

    def counting(self: JwtVerifier, token: str) -> int:
        nonlocal calls
        calls += 1
        return verify(self, token)

    monkeypatch.setattr(JwtVerifier, "user_id", counting)
    async with jwt_client(jwt_settings(rate_limit_per_minute=100), pool=_EmptyPool()) as client:
        assert (await client.get(FRIDGE, headers=bearer(make_token(signing_key)))).status_code == 200
    assert calls == 1


async def test_unreachable_jwks_is_tried_once_per_request(
    monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey
) -> None:
    """auth 서버가 죽으면 요청 하나가 JWKS 를 한 번만 받으려 합니다.

    두 미들웨어가 따로 검증하면 요청마다 JWKS 제한 시간(기본 5초)을 두 번 기다리게 됩니다.
    """
    attempts = 0

    def fail(self: jwt.PyJWKClient) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        raise PyJWKClientConnectionError("down")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
    async with jwt_client(jwt_settings(rate_limit_per_minute=100), pool=object()) as client:
        assert (await client.get(FRIDGE, headers=bearer(make_token(signing_key)))).status_code == 503
    assert attempts == 1


async def test_rate_limit_keys_by_verified_user(signing_key: rsa.RSAPrivateKey) -> None:
    """rate limit 키는 검증을 통과한 사용자입니다. 다른 사용자는 서로 한도를 나누지 않습니다."""
    async with jwt_client(jwt_settings(rate_limit_per_minute=1), pool=_EmptyPool()) as client:
        first = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="1")))
        assert first.status_code == 200
        again = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="1")))
        assert again.status_code == 429
        other = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="2")))
        assert other.status_code == 200


async def test_forged_token_cannot_exhaust_victim_bucket(
    signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
) -> None:
    """위조 토큰은 IP 버킷으로 세므로 대상 사용자의 정상 토큰은 그대로 통과합니다."""
    async with jwt_client(jwt_settings(rate_limit_per_minute=1), pool=_EmptyPool()) as client:
        forged = await client.get(FRIDGE, headers=bearer(make_token(other_key, sub="7")))
        assert forged.status_code == 401
        victim = await client.get(FRIDGE, headers=bearer(make_token(signing_key, sub="7")))
        assert victim.status_code == 200
        # IP 버킷은 위조 요청이 이미 썼으므로 익명 요청은 막힙니다
        anonymous = await client.get(MISSING)
        assert anonymous.status_code == 429


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("42", 42), (42, 42), ("0", None), ("-1", None), ("²", None), ("４２", None), ("9" * 5000, None), (True, None)],
)
def test_parse_user_id_accepts_ascii_digits_only(raw: object, expected: int | None) -> None:
    """유니코드 숫자나 너무 긴 숫자열은 예외 없이 None 입니다."""
    assert _parse_user_id(raw) == expected
