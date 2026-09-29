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

JWKS 는 PyJWT 의 `PyJWKClient` 가 `kid` 기준으로 캐시합니다. 모르는 `kid` 가 오면 한 번
다시 받으므로 auth 서버의 키 교체를 재기동 없이 따라갑니다. 받기는 동기 urllib 이라
의존성을 sync `def` 로 두어 FastAPI 가 스레드풀에서 돌리게 합니다(이벤트 루프를 막지 않음).
"""

from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from serving.config import Settings

USER_ID_HEADER = "X-User-Id"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "bearer "

logger = logging.getLogger("serving.auth")


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")


def _parse_user_id(raw: object) -> int | None:
    """양의 정수 문자열(또는 정수)만 사용자 id 로 받습니다. 아니면 None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, str) and raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return None


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
            cache_keys=True,
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


def get_current_user_id(
    request: Request,
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> int:
    """사용자 id 를 돌려줍니다. JWT 가 켜져 있으면 Bearer 토큰만, 아니면 X-User-Id 만 봅니다."""
    verifier = _verifier(request)
    if verifier is None:
        return _header_user_id(x_user_id)
    token = bearer_token(authorization)
    if token is None:
        raise _unauthorized()
    return verifier.user_id(token)


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
    token = bearer_token(authorization)
    if token is None:
        raise _unauthorized()
    return verifier.user_id(token)


OptionalUserId = Annotated[int, Depends(get_optional_user_id)]


def peek_user_key(request: Request) -> str | None:
    """미들웨어(rate limit·액세스 로그)용 사용자 키. **검증하지 않습니다.**

    Bearer 토큰이 있으면 서명 확인 없이 `sub` 만 읽고, 없으면 X-User-Id 를 씁니다.
    인가에는 쓰지 않고 요청을 사용자별로 묶는 키로만 씁니다. 위조 토큰은 어차피
    의존성 단계에서 401 이라, 여기서 얻는 것은 자기 요청의 묶음뿐입니다.
    """
    token = bearer_token(request.headers.get(AUTHORIZATION_HEADER))
    if token is not None:
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        user_id = _parse_user_id(claims.get("sub"))
        return None if user_id is None else str(user_id)
    header = request.headers.get(USER_ID_HEADER)
    return header if _parse_user_id(header) is not None else None
