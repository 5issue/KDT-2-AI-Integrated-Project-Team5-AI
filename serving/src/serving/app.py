"""FastAPI 앱 조립.

커넥션 풀은 lifespan 에서 한 번 만들고 앱 상태에 붙입니다. 요청마다 새로 붙으면
Neon 의 커넥션 한도를 금방 넘습니다.

DATABASE_URL 이 없으면 풀 없이 뜹니다. 그래야 DB 없이도 앱을 띄워 라우팅과 스키마를
확인할 수 있습니다. 이때 DB 가 필요한 엔드포인트는 503 으로 답합니다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rag_lab.reason_service import OpenRouterReasonClient, ReasonSettings
from serving import __version__
from serving.config import Settings, get_settings
from serving.db import create_pool, mask_dsn
from serving.exceptions import API_PREFIX, register_exception_handlers
from serving.ratelimit import RateLimitMiddleware
from serving.request_log import RequestLogMiddleware
from serving.routers import fridge, health, home, products, recipes, recommendations

logger = logging.getLogger("serving")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱이 뜰 때 풀을 만들고 내려갈 때 정리합니다."""
    # create_app 에 주입된 설정을 씁니다. get_settings() 를 다시 읽으면
    # 테스트나 팩토리에서 넘긴 설정(종료 유예 0 등)이 무시됩니다.
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    app.state.pool = None
    # 추천 이유 LLM 클라이언트. 키가 없어 else 를 타지 않아도 종료 코드가 AttributeError 를
    # 내지 않도록 세 값을 먼저 초기화합니다.
    app.state.reason_http = None
    app.state.reason_client = None
    app.state.ingredient_vocabulary = frozenset()

    try:
        dsn = settings.require_database_url()
    except RuntimeError as exc:
        logger.warning("DB 없이 기동합니다: %s", exc)
    else:
        # 로그에는 마스킹한 형태만 남깁니다.
        logger.info("커넥션 풀 생성: %s", mask_dsn(dsn))
        app.state.pool = await create_pool(settings)

    try:
        reason_settings = ReasonSettings.from_env(settings.reason_environ())
    except RuntimeError as exc:
        logger.warning("추천 이유는 규칙 기반 문구만 씁니다: %s", exc)
    else:
        # httpx.AsyncClient 는 앱 수명 동안 하나만 씁니다. 키는 클라이언트 안에만 있고 로그에 남지 않습니다.
        app.state.reason_http = httpx.AsyncClient()
        app.state.reason_client = OpenRouterReasonClient(app.state.reason_http, reason_settings)
        if app.state.pool is not None:
            # 환각 검사 사전. 세 카드 어디에도 없는 재료를 지어냈는지 잡습니다. 한 번만 읽습니다.
            async with app.state.pool.acquire() as conn:
                rows = await conn.fetch("SELECT name FROM ingredient")
            app.state.ingredient_vocabulary = frozenset(str(row["name"]) for row in rows)
        logger.info(
            "추천 이유 LLM 생성 사용: model=%s, 사전 %d종",
            reason_settings.model,
            len(app.state.ingredient_vocabulary),
        )

    try:
        yield
    finally:
        # EKS 스팟 회수 등으로 SIGTERM 을 받으면 uvicorn 이 처리 중 요청을 끝낸 뒤
        # 여기로 들어옵니다. 시작/완료를 로그로 남겨 강제 종료(로그 없음)와 구분합니다.
        logger.info("graceful shutdown 시작 - 처리 중 요청 완료됨, 리소스 정리")
        # SIGTERM 은 ALB 의 타깃 제외보다 먼저 도착합니다. 제외가 전파될 때까지
        # 기다렸다가 정리해야 그 사이 들어온 요청이 502 로 끊기지 않습니다.
        if settings.shutdown_delay_seconds > 0:
            await asyncio.sleep(settings.shutdown_delay_seconds)
        if app.state.reason_http is not None:
            await app.state.reason_http.aclose()
            app.state.reason_http = None
            app.state.reason_client = None
        if app.state.pool is not None:
            await app.state.pool.close()
            app.state.pool = None
            logger.info("커넥션 풀 정리 완료")
        logger.info("graceful shutdown 완료")


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
    app.state.settings = settings

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
    app.include_router(fridge.router, prefix=API_PREFIX)
    return app


app = create_app()
