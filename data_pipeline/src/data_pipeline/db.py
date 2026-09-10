"""Neon PostgreSQL 비동기 연결 + 연결 점검 유틸리티.

Neon 을 asyncpg 로 붙일 때 걸리는 세 가지를 여기서 한 번에 처리합니다.

1. Neon 콘솔이 주는 DSN 은 `postgresql://` 이라 SQLAlchemy asyncpg 방언으로 바꿔야 합니다.
2. DSN 에 붙어 오는 `sslmode` / `channel_binding` 쿼리 파라미터를 asyncpg 는 모릅니다.
   제거한 뒤 asyncpg 의 `ssl` 인자로 넘겨야 합니다.
3. pooler 엔드포인트(`-pooler`)는 PgBouncer transaction 모드라 prepared statement 를
   재사용할 수 없습니다. 캐시를 끄고 statement 이름을 매번 새로 만들어야 합니다.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from data_pipeline.config import Settings, get_settings

# asyncpg 가 이해하지 못해 URL 에서 걷어내야 하는 libpq 전용 파라미터.
_LIBPQ_ONLY_PARAMS = frozenset({"sslmode", "channel_binding", "target_session_attrs", "options"})

# ai_context/database_schema.md v0.1.0 기준으로 있어야 하는 테이블.
EXPECTED_TABLES: tuple[str, ...] = (
    "app_user",
    "category",
    "ingredient",
    "order_header",
    "order_item",
    "product",
    "product_ingredient",
    "product_popularity",
    "recipe",
    "recipe_ingredient",
    "recipe_product",
    "storage_guideline",
    "user_fridge",
    "user_product_affinity",
)


def mask_dsn(url: str) -> str:
    """로그에 남겨도 되는 형태로 DSN 을 가립니다. 호스트와 자격증명은 남기지 않습니다."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparsable-dsn>"
    database = parts.path.lstrip("/") or "<unknown>"
    pooled = "pooled" if "-pooler." in (parts.hostname or "") else "direct"
    return f"{parts.scheme}://***@***/{database} ({pooled})"


def normalize_neon_url(url: str) -> tuple[str, dict[str, Any]]:
    """Neon DSN 을 (SQLAlchemy asyncpg URL, connect_args) 로 변환합니다."""
    parts = urlsplit(url)

    scheme = parts.scheme
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+asyncpg"
    if scheme != "postgresql+asyncpg":
        raise ValueError(f"지원하지 않는 DSN 스킴입니다: {parts.scheme!r} (postgresql+asyncpg 를 사용하세요)")

    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    kept = [(key, value) for key, value in query if key.lower() not in _LIBPQ_ONLY_PARAMS]
    dropped = {key.lower(): value for key, value in query if key.lower() in _LIBPQ_ONLY_PARAMS}

    connect_args: dict[str, Any] = {}

    # Neon 은 항상 TLS 를 요구합니다. sslmode 를 asyncpg 의 ssl 인자로 옮깁니다.
    sslmode = dropped.get("sslmode", "require")
    connect_args["ssl"] = "verify-full" if sslmode == "verify-full" else "require"

    if "-pooler." in (parts.hostname or ""):
        # PgBouncer transaction 모드 대응.
        connect_args["prepared_statement_cache_size"] = 0
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid4()}__"

    normalized = urlunsplit((scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return normalized, connect_args


def create_engine(
    *,
    direct: bool = False,
    settings: Settings | None = None,
    echo: bool = False,
    **engine_kwargs: Any,
) -> AsyncEngine:
    """설정에 있는 DSN 으로 AsyncEngine 을 만듭니다. direct=True 면 비-pooler 엔드포인트를 씁니다."""
    settings = settings or get_settings()
    url, connect_args = normalize_neon_url(settings.require_database_url(direct=direct))
    connect_args.update(engine_kwargs.pop("connect_args", {}))
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        connect_args=connect_args,
        **engine_kwargs,
    )


@asynccontextmanager
async def engine_scope(*, direct: bool = False, settings: Settings | None = None) -> AsyncIterator[AsyncEngine]:
    """스크립트에서 쓰기 좋은 엔진 컨텍스트. 빠져나갈 때 커넥션 풀을 정리합니다."""
    engine = create_engine(direct=direct, settings=settings)
    try:
        yield engine
    finally:
        await engine.dispose()


@dataclass(slots=True)
class ConnectionReport:
    """DB 연결 점검 결과. 자격증명이나 호스트는 담지 않습니다."""

    dsn_label: str
    server_version: str
    database: str
    user: str
    latency_ms: float
    vector_extension: str | None = None
    present_tables: tuple[str, ...] = ()
    missing_tables: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """스키마까지 기대대로면 True."""
        return not self.missing_tables and self.vector_extension is not None

    def render(self) -> str:
        """사람이 읽을 리포트 문자열."""
        lines = [
            f"연결 대상   : {self.dsn_label}",
            f"서버 버전   : {self.server_version}",
            f"database    : {self.database} (user={self.user})",
            f"왕복 지연   : {self.latency_ms:.1f} ms",
            f"pgvector    : {self.vector_extension or '미설치'}",
            f"테이블      : {len(self.present_tables)}개 확인",
        ]
        if self.missing_tables:
            lines.append(f"누락 테이블 : {', '.join(self.missing_tables)}")
        lines.extend(f"참고        : {note}" for note in self.notes)
        return "\n".join(lines)


async def check_connection(
    *,
    direct: bool = False,
    settings: Settings | None = None,
    expected_tables: Sequence[str] = EXPECTED_TABLES,
) -> ConnectionReport:
    """Neon 에 실제로 붙어서 버전, pgvector, 테이블 존재 여부를 확인합니다."""
    settings = settings or get_settings()
    dsn_label = mask_dsn(settings.require_database_url(direct=direct))

    async with engine_scope(direct=direct, settings=settings) as engine:
        started = time.perf_counter()
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version()"))).scalar_one()
            database, user = (await conn.execute(text("SELECT current_database(), current_user"))).one()
            latency_ms = (time.perf_counter() - started) * 1000

            vector_version = (
                await conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            ).scalar_one_or_none()

            rows = (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                    )
                )
            ).scalars()
            actual = {str(name) for name in rows}

    present = tuple(name for name in expected_tables if name in actual)
    missing = tuple(name for name in expected_tables if name not in actual)

    report = ConnectionReport(
        dsn_label=dsn_label,
        server_version=str(version).split(" on ")[0],
        database=str(database),
        user=str(user),
        latency_ms=latency_ms,
        vector_extension=str(vector_version) if vector_version is not None else None,
        present_tables=present,
        missing_tables=missing,
    )
    if settings.neon_branch:
        report.notes.append(f"Neon 브랜치: {settings.neon_branch}")
    extra = sorted(actual - set(expected_tables))
    if extra:
        report.notes.append(f"스키마 문서에 없는 테이블 {len(extra)}개: {', '.join(extra[:10])}")
    return report
