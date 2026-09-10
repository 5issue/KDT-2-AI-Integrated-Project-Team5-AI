"""FastAPI 의존성."""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

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


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
