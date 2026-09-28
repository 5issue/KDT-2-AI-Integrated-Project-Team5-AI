"""헬스체크 엔드포인트.

`/health` 는 프로세스가 살아 있는지(liveness), `/health/db` 는 DB 까지 붙는지(readiness)
확인합니다. 둘을 나눠 두면 DB 가 잠깐 흔들릴 때 컨테이너가 통째로 재시작되지 않습니다.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from serving import __version__
from serving.db import check_health
from serving.dependencies import PoolDep, SettingsDep
from serving.reason_runtime import ReasonRuntime
from serving.schemas import DbHealthResponse, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def read_health(settings: SettingsDep) -> HealthResponse:
    """프로세스 liveness. DB 를 건드리지 않습니다."""
    return HealthResponse(version=__version__, environment=settings.environment)


@router.get("/health/db", response_model=DbHealthResponse)
async def read_db_health(
    request: Request, pool: PoolDep, settings: SettingsDep, response: Response
) -> DbHealthResponse:
    """DB readiness. 실패해도 예외 대신 503 과 상세 없는 사유를 돌려줍니다.

    추천 이유 LLM 이 꺼져 있으면 여기서도 다시 켜 봅니다(재시도 간격마다 한 번). 트래픽이 없어도
    준비 상태 프로브가 복구를 이어 가게 하려는 것입니다. 결과는 ``ok`` 에 넣지 않습니다.
    """
    health = await check_health(pool, settings=settings)
    runtime: ReasonRuntime = request.app.state.reason_runtime
    if health.ok:
        await runtime.try_enable(pool)
    else:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return DbHealthResponse(
        ok=health.ok,
        latency_ms=round(health.latency_ms, 2),
        database=health.database,
        server_version=health.server_version,
        pool_size=health.pool_size,
        pool_idle=health.pool_idle,
        detail=health.detail,
        notes=health.notes,
        reason_llm="on" if runtime.enabled else "off",
    )
