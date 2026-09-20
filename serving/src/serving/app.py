"""FastAPI 앱 조립.

커넥션 풀은 lifespan 에서 한 번 만들고 앱 상태에 붙입니다. 요청마다 새로 붙으면
Neon 의 커넥션 한도를 금방 넘습니다.

DATABASE_URL 이 없으면 풀 없이 뜹니다. 그래야 DB 없이도 앱을 띄워 라우팅과 스키마를
확인할 수 있습니다. 이때 DB 가 필요한 엔드포인트는 503 으로 답합니다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from serving import __version__
from serving.config import Settings, get_settings
from serving.db import create_pool, mask_dsn
from serving.exceptions import API_PREFIX, register_exception_handlers
from serving.ratelimit import RateLimitMiddleware
from serving.request_log import RequestLogMiddleware
from serving.routers import health, home, products, recipes, recommendations

logger = logging.getLogger("serving")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱이 뜰 때 풀을 만들고 내려갈 때 정리합니다."""
    settings: Settings = get_settings()
    app.state.pool = None

    try:
        dsn = settings.require_database_url()
    except RuntimeError as exc:
        logger.warning("DB 없이 기동합니다: %s", exc)
    else:
        # 로그에는 마스킹한 형태만 남깁니다.
        logger.info("커넥션 풀 생성: %s", mask_dsn(dsn))
        app.state.pool = await create_pool(settings)

    try:
        yield
    finally:
        if app.state.pool is not None:
            await app.state.pool.close()
            app.state.pool = None


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱 인스턴스를 만듭니다."""
    settings = settings or get_settings()
    app = FastAPI(
        title="5issue AI 추천 API",
        version=__version__,
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_url else None,
        lifespan=lifespan,
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=True,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    # 나중에 추가한 미들웨어가 바깥에 섭니다. 요청 로그가 429 응답까지 보도록
    # RequestLog 를 RateLimit 뒤(= 더 바깥)에 둡니다.
    app.add_middleware(
        RateLimitMiddleware,
        default_per_minute=settings.rate_limit_per_minute,
        reco_per_minute=settings.rate_limit_reco_per_minute,
    )

    app.add_middleware(RequestLogMiddleware)

    register_exception_handlers(app)

    # 헬스체크는 envelope 적용 대상에서 제외하므로 prefix 밖에 둡니다.
    app.include_router(health.router)
    app.include_router(recommendations.router, prefix=API_PREFIX)
    app.include_router(home.router, prefix=API_PREFIX)
    app.include_router(products.router, prefix=API_PREFIX)
    app.include_router(recipes.router, prefix=API_PREFIX)
    return app


app = create_app()
