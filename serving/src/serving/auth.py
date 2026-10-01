"""사용자 식별 의존성.

두 방식을 지원하고 설정(`JWT_JWKS_URL`)으로 고릅니다. 방식이 바뀌면 이 모듈만 바꾸면
되도록 한 곳에 모아 둡니다.

- **Bearer JWT** (BE 보안 정책, 2026-09-29 BE 답변): `Authorization: Bearer <JWT>`.
  auth 서버의 `GET /.well-known/jwks.json` 으로 받은 공개키로 서명을 검증하고 `sub`
  (user_db `users.id`)를 사용자 id 로 씁니다. 동기 호출은 JWT 원문을 그대로 전파하므로
  BFF 든 BE 서비스든 같은 토큰으로 부릅니다.
- **X-User-Id 헤더** (로컬·JWKS 미설정): BFF 만 FastAPI 에 닿는 private network 전제입니다.
  2026-09-22 FE 협의 방식이며, JWT 가 켜지면 **무시합니다**. 둘을 같이 받으면 헤더 한 줄로
  서명 검증을 우회할 수 있기 때문입니다.

## 검증은 요청당 한 번, 미들웨어에서

rate limit 과 액세스 로그는 의존성보다 먼저 도는 미들웨어라 사용자 키가 그 시점에 필요합니다.
서명 없이 `sub` 만 읽어 키로 쓰면 위조 토큰으로 남의 버킷을 소진시킬 수 있어(CodeRabbit PR #45),
미들웨어에서도 **서명까지 검증**하고 결과를 `request.state` 에 남깁니다. 의존성은 그 결과를
재사용해 같은 토큰을 두 번 검증하지 않습니다. 검증 실패 요청은 IP 버킷으로 셉니다.

## JWKS 캐시

`PyJWKClient` 의 JWK Set 캐시(`lifespan`)만 씁니다. 키별 LRU 캐시(`cache_keys`)는 TTL 이 없어
JWKS 에서 뺀 키가 영원히 살아남으므로 끕니다. 모르는 `kid` 가 오면 한 번 다시 받아 키 교체를
재기동 없이 따라갑니다. 받기는 동기 urllib 이라 스레드풀에서 돌려 이벤트 루프를 막지 않습니다.
"""

from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError
from starlette.concurrency import run_in_threadpool

from serving.config import Settings
from serving.constants import PG_BIGINT_MAX

USER_ID_HEADER = "X-User-Id"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "bearer "
# request.state 에 남기는 검증 결과. 의존성과 미들웨어가 공유합니다.
STATE_USER_ID = "auth_user_id"
STATE_ERROR = "auth_error"

logger = logging.getLogger("serving.auth")


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")


def _parse_user_id(raw: object) -> int | None:
    """ASCII 숫자만으로 된 양의 정수(또는 정수)만 사용자 id 로 받습니다. 아니면 None.

    `str.isdigit()` 는 "²" 같은 유니코드 숫자도 참이라 `int()` 가 터집니다. ASCII 를 먼저 보고,
    자릿수 제한 등으로 변환이 실패해도 예외 대신 None 입니다. bigint 를 넘는 값도 None 입니다.
    DB 에 넘기면 인자 변환에서 DataError(500)가 나기 때문입니다.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        user_id = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit():
        try:
            user_id = int(raw)
        except ValueError:
            return None
    else:
        return None
    return user_id if 1 <= user_id <= PG_BIGINT_MAX else None


def bearer_token(authorization: str | None) -> str | None:
    """`Authorization` 헤더에서 Bearer 토큰만 꺼냅니다. 형식이 아니면 None."""
    if authorization is None or not authorization[: len(BEARER_PREFIX)].lower() == BEARER_PREFIX:
        return None
    token = authorization[len(BEARER_PREFIX) :].strip()
    return token or None


class JwtVerifier:
    """JWKS 로 JWT 서명을 검증하고 사용자 id 를 꺼냅니다. 앱 상태(`app.state.jwt_verifier`)에 하나 둡니다."""

    def __init__(self, settings: Settings) -> None:
        self._client = jwt.PyJWKClient(
            settings.jwt_jwks_url.strip(),
            cache_keys=False,
            lifespan=settings.jwt_jwks_cache_seconds,
            timeout=settings.jwt_jwks_timeout_seconds,
        )
        self._algorithms = list(settings.jwt_algorithms)
        self._issuer = settings.jwt_issuer.strip() or None
        self._audience = settings.jwt_audience.strip() or None
        self._leeway = settings.jwt_leeway_seconds

    def user_id(self, token: str) -> int:
        """서명·만료·발급자(설정 시)·대상(설정 시)을 검증하고 `sub` 를 돌려줍니다.

        실패 사유는 로그에 예외 이름만 남기고 응답은 401 하나로 통일합니다. 사유를 구분해
        내면 토큰 위조를 시도하는 쪽에 힌트가 됩니다. auth 서버에 닿지 못한 경우만 503 입니다.
        """
        try:
            signing_key = self._client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={"require": ["exp", "sub"], "verify_aud": self._audience is not None},
            )
        except PyJWKClientConnectionError as exc:
            logger.error("JWKS 를 받지 못했습니다: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="인증 서버에 연결할 수 없습니다."
            ) from exc
        except (PyJWKClientError, jwt.PyJWTError) as exc:
            logger.info("JWT 검증 실패: %s", type(exc).__name__)
            raise _unauthorized() from exc

        user_id = _parse_user_id(claims.get("sub"))
        if user_id is None:
            logger.info("JWT sub 가 사용자 id 형식이 아닙니다")
            raise _unauthorized()
        return user_id


def _verifier(request: Request) -> JwtVerifier | None:
    return getattr(request.app.state, "jwt_verifier", None)


def _header_user_id(x_user_id: str | None) -> int:
    """X-User-Id 방식. 없거나 형식이 틀리면 401 입니다."""
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        raise _unauthorized()
    return user_id


def _cached(request: Request) -> int | None:
    """미들웨어가 남긴 검증 결과. 실패였다면 그 예외를 다시 던집니다. 없으면 None."""
    error = getattr(request.state, STATE_ERROR, None)
    if error is not None:
        raise error
    return getattr(request.state, STATE_USER_ID, None)


def _verify_and_remember(request: Request, verifier: JwtVerifier, token: str) -> int:
    """토큰을 검증하고 결과(성공·실패 모두)를 request.state 에 남깁니다."""
    try:
        user_id = verifier.user_id(token)
    except HTTPException as exc:
        setattr(request.state, STATE_ERROR, exc)
        raise
    setattr(request.state, STATE_USER_ID, user_id)
    return user_id


async def resolve_user_key(request: Request) -> str | None:
    """미들웨어(rate limit·액세스 로그)용 사용자 키. 검증을 통과한 사용자만 돌려줍니다.

    JWT 모드에서는 서명까지 검증하고 결과를 request.state 에 남겨 의존성이 재사용합니다.
    토큰이 없거나 검증에 실패하면 None 이라 IP 버킷으로 셉니다. 헤더 모드에서는 형식이 맞는
    X-User-Id 만 키로 씁니다.
    """
    verifier = _verifier(request)
    if verifier is None:
        header_id = _parse_user_id(request.headers.get(USER_ID_HEADER))
        return None if header_id is None else str(header_id)
    # 액세스 로그와 rate limit 미들웨어가 둘 다 부릅니다. 앞에서 검증했으면 그 결과를 씁니다.
    # 다시 검증하면 인증 서버가 죽었을 때 요청마다 JWKS 제한 시간을 두 번 기다립니다.
    if getattr(request.state, STATE_ERROR, None) is not None:
        return None
    if (verified := getattr(request.state, STATE_USER_ID, None)) is not None:
        return str(verified)
    token = bearer_token(request.headers.get(AUTHORIZATION_HEADER))
    if token is None:
        return None
    try:
        user_id = await run_in_threadpool(_verify_and_remember, request, verifier, token)
    except HTTPException:
        return None
    return str(user_id)


def get_current_user_id(
    request: Request,
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> int:
    """사용자 id 를 돌려줍니다. JWT 가 켜져 있으면 Bearer 토큰만, 아니면 X-User-Id 만 봅니다."""
    verifier = _verifier(request)
    if verifier is None:
        return _header_user_id(x_user_id)
    cached = _cached(request)
    if cached is not None:
        return cached
    token = bearer_token(authorization)
    if token is None:
        raise _unauthorized()
    return _verify_and_remember(request, verifier, token)


CurrentUserId = Annotated[int, Depends(get_current_user_id)]


def get_optional_user_id(
    request: Request,
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> int:
    """비로그인 허용 엔드포인트용. 자격 증명이 없으면 0(미사용)을 돌려줍니다.

    있는데 틀린 경우는 조용히 무시하지 않고 401 입니다.
    잘못 보낸 요청을 익명으로 처리하면 원인을 찾기 어려워집니다.
    """
    verifier = _verifier(request)
    if verifier is None:
        return 0 if x_user_id is None else _header_user_id(x_user_id)
    if authorization is None:
        return 0
    cached = _cached(request)
    if cached is not None:
        return cached
    token = bearer_token(authorization)
    if token is None:
        raise _unauthorized()
    return _verify_and_remember(request, verifier, token)


OptionalUserId = Annotated[int, Depends(get_optional_user_id)]
