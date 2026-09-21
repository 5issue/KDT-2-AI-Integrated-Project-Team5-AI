"""요청 id + 구조화 액세스 로그.

500 이 나도 추적할 수 있게, 모든 응답에 X-Request-Id 를 싣고 요청마다 JSON 한 줄을
남깁니다. BFF 가 X-Request-Id 를 보내면 그대로 써서 경계 간 추적이 이어집니다.

- 들어온 id 는 형식 검증 후에만 신뢰합니다 (개행 등 로그 인젝션 방지).
- 헬스체크는 로그를 남기지 않습니다 (프로브 노이즈).
- 처리되지 않은 예외는 여기서 request_id 와 함께 에러 로그를 남기고 다시 던집니다.
  (500 envelope 은 바깥의 ServerErrorMiddleware 가 만들므로 응답 헤더에는 id 를
  싣지 못합니다. 로그로 추적합니다.)
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
_VALID_REQUEST_ID = re.compile(r"^[0-9A-Za-z._-]{8,64}$")
_SKIP_PATHS = frozenset({"/health", "/health/db"})

access_logger = logging.getLogger("serving.access")


def _client_ip(request: Request) -> str | None:
    """실제 유저 IP. LB/인그레스를 거치면 X-Forwarded-For 첫 값이 원 클라이언트입니다.

    이 값은 위조 가능한 헤더라 추적(로그)용으로만 쓰고, 인증·차단 판단에는 쓰지 않습니다.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _resolve_request_id(incoming: str | None) -> str:
    if incoming and _VALID_REQUEST_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex[:16]


class RequestLogMiddleware(BaseHTTPMiddleware):
    """요청 id 부여 + 액세스 로그 한 줄."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        if request.url.path in _SKIP_PATHS:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response

        start = time.perf_counter()
        entry = {
            "request_id": request_id,
            "client_ip": _client_ip(request),
            "method": request.method,
            "path": request.url.path,
            "user_id": request.headers.get("X-User-Id"),
        }
        try:
            response = await call_next(request)
        except Exception as exc:
            entry.update(
                status=500,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
                error=type(exc).__name__,
            )
            # traceback 을 request_id 와 같은 레코드에 묶습니다. uvicorn 의 재발생 로그에는
            # 요청 맥락이 없어 여기서 남기는 것이 추적 가능한 유일한 지점입니다.
            access_logger.error(json.dumps(entry, ensure_ascii=False), exc_info=True)
            raise

        entry.update(
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        access_logger.info(json.dumps(entry, ensure_ascii=False))
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
