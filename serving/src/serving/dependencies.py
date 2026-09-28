"""FastAPI 의존성."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from rag_lab.reason_service import OpenRouterReasonClient
from serving.config import Settings, get_settings


def get_pool(request: Request) -> asyncpg.Pool:
    """앱 상태에 붙은 커넥션 풀을 꺼냅니다. 없으면 503 으로 답합니다."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="데이터베이스에 연결되어 있지 않습니다.",
        )
    return pool


@dataclass(frozen=True, slots=True)
class ReasonContext:
    """추천 이유 생성에 필요한 앱 상태. ``client`` 가 None 이면 규칙 기반 문구만 씁니다."""

    client: OpenRouterReasonClient | None
    vocabulary: frozenset[str]


def get_reason_context(request: Request) -> ReasonContext:
    """lifespan 이 붙여 둔 LLM 클라이언트와 환각 검사 사전을 꺼냅니다. 없으면 규칙 문구 모드입니다."""
    state = request.app.state
    return ReasonContext(
        client=getattr(state, "reason_client", None),
        vocabulary=getattr(state, "ingredient_vocabulary", frozenset()),
    )


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]
ReasonDep = Annotated[ReasonContext, Depends(get_reason_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
