"""asyncpg 커넥션 풀.

SQLAlchemy 를 거치지 않고 asyncpg 를 직접 씁니다. 서빙 경로에서는 ORM 매핑 비용 없이
정해진 SQL 만 돌리면 되기 때문입니다.

Neon 을 붙일 때 걸리는 지점은 data_pipeline/db.py 와 같습니다.
- DSN 의 `sslmode` / `channel_binding` 은 asyncpg 가 모르므로 `ssl` 인자로 옮깁니다.
- pooler 엔드포인트는 PgBouncer transaction 모드라 prepared statement 를 재사용할 수 없어
  statement 캐시를 꺼야 합니다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from serving.config import Settings, get_settings

_LIBPQ_ONLY_PARAMS = frozenset({"sslmode", "channel_binding", "target_session_attrs", "options"})


def mask_dsn(url: str) -> str:
    """로그에 남겨도 되는 형태로 DSN 을 가립니다. 호스트와 자격증명은 남기지 않습니다."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparsable-dsn>"
    database = parts.path.lstrip("/") or "<unknown>"
    pooled = "pooled" if "-pooler." in (parts.hostname or "") else "direct"
    return f"postgresql://***@***/{database} ({pooled})"


def normalize_neon_dsn(url: str) -> tuple[str, dict[str, Any]]:
    """Neon DSN 을 (asyncpg DSN, connect kwargs) 로 변환합니다."""
    parts = urlsplit(url)

    scheme = parts.scheme.split("+", 1)[0]
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"지원하지 않는 DSN 스킴입니다: {parts.scheme!r}")

    query = list(parse_qsl(parts.query, keep_blank_values=True))
    kept = [(key, value) for key, value in query if key.lower() not in _LIBPQ_ONLY_PARAMS]
    dropped = {key.lower(): value for key, value in query if key.lower() in _LIBPQ_ONLY_PARAMS}

    sslmode = dropped.get("sslmode", "require")
    if sslmode in {"disable", "allow"}:
        # 클러스터 내부 DB(CloudNativePG)처럼 파드 간 평문 통신 구간에서 씁니다.
        connect_kwargs: dict[str, Any] = {"ssl": False}
    else:
        connect_kwargs = {"ssl": "verify-full" if sslmode == "verify-full" else "require"}

    if "-pooler." in (parts.hostname or ""):
        # PgBouncer transaction 모드에서는 prepared statement 를 재사용할 수 없습니다.
        connect_kwargs["statement_cache_size"] = 0
        connect_kwargs["max_cacheable_statement_size"] = 0

    dsn = urlunsplit(("postgresql", parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return dsn, connect_kwargs


async def create_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """설정대로 커넥션 풀을 만듭니다."""
    settings = settings or get_settings()
    dsn, connect_kwargs = normalize_neon_dsn(settings.require_database_url())
    pool = await asyncpg.create_pool(
        dsn,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout,
        command_timeout=settings.db_command_timeout,
        **connect_kwargs,
    )
    if pool is None:
        raise RuntimeError("커넥션 풀 생성에 실패했습니다.")
    return pool


@dataclass(slots=True)
class DbHealth:
    """DB 헬스체크 결과. 자격증명이나 호스트는 담지 않습니다."""

    ok: bool
    latency_ms: float
    database: str | None = None
    server_version: str | None = None
    pool_size: int | None = None
    pool_idle: int | None = None
    detail: str | None = None
    notes: list[str] = field(default_factory=list)


async def check_health(pool: asyncpg.Pool, *, settings: Settings | None = None) -> DbHealth:
    """풀에서 커넥션을 하나 빌려 왕복 한 번을 재 봅니다."""
    settings = settings or get_settings()
    started = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT current_database() AS db, version() AS version")
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        # 예외 메시지에 호스트가 들어갈 수 있어 타입만 남깁니다.
        return DbHealth(
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            detail=type(exc).__name__,
        )

    health = DbHealth(
        ok=True,
        latency_ms=(time.perf_counter() - started) * 1000,
        database=str(row["db"]) if row else None,
        server_version=str(row["version"]).split(" on ")[0] if row else None,
        pool_size=pool.get_size(),
        pool_idle=pool.get_idle_size(),
    )
    if settings.neon_branch:
        health.notes.append(f"Neon 브랜치: {settings.neon_branch}")
    return health
