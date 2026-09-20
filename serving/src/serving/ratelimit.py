"""요청 rate limit (슬라이딩 윈도, in-memory).

명세 에러표의 429 계약을 실제로 동작하게 합니다. 식별 키는 X-User-Id 가 있으면
사용자, 없으면 클라이언트 IP 입니다. 추천 엔드포인트는 더 낮은 한도를 씁니다
(비용이 큰 경로 보호).

프로세스별 카운터라 멀티 워커/replica 에서는 실효 한도가 그 배수가 됩니다.
MVP(단일 컨테이너) 전제이고, 스케일 아웃 시 Redis 백엔드로 교체합니다.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from serving.envelope import ApiResponse, ErrorCode

API_PREFIX = "/api/v1"
RECO_PREFIX = "/api/v1/recommendations"
WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    """키별 최근 60초 요청 수를 셉니다."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def try_acquire(self, key: str, now: float | None = None) -> float | None:
        """허용이면 None, 초과면 다시 시도까지 남은 초를 돌려줍니다."""
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self.limit:
            return WINDOW_SECONDS - (now - hits[0])
        hits.append(now)
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """/api/v1 요청에 한도를 적용합니다. 한도 0 은 비활성입니다."""

    def __init__(self, app, default_per_minute: int, reco_per_minute: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._default = SlidingWindowLimiter(default_per_minute) if default_per_minute > 0 else None
        self._reco = SlidingWindowLimiter(reco_per_minute) if reco_per_minute > 0 else None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not (path == API_PREFIX or path.startswith(f"{API_PREFIX}/")):
            return await call_next(request)

        limiter = self._reco if path.startswith(RECO_PREFIX) and self._reco else self._default
        if limiter is None:
            return await call_next(request)

        key = request.headers.get("X-User-Id") or (request.client.host if request.client else "unknown")
        retry_after = limiter.try_acquire(key)
        if retry_after is not None:
            payload: ApiResponse[None] = ApiResponse.failure(ErrorCode.TOO_MANY_REQUESTS)
            return JSONResponse(
                status_code=429,
                content=payload.model_dump(mode="json"),
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )
        return await call_next(request)
