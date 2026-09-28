"""추천 이유 LLM 을 켜고, 꺼져 있으면 스스로 다시 켜는 동작 테스트. DB 없이 돕니다.

PR #42 리뷰 B: 기동 때 재료 사전 조회가 한 번 실패하면 파드가 사는 동안 LLM 이 꺼진 채였습니다.
응답은 200 이라 운영자가 모르는 채 규칙 문구만 나갔고, 재시작해야 복구됐습니다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from rag_lab.reason_service import ReasonSettings
from serving import reason_runtime
from serving.config import Settings
from serving.reason_runtime import RETRY_SECONDS, ReasonRuntime


class _ScriptedPool:
    """사전 조회 결과를 차례로 돌려주는 풀. 예외면 던지고, 목록이면 그 이름들을 냅니다."""

    def __init__(self, *results: list[str] | Exception, gate: asyncio.Event | None = None) -> None:
        self.results = list(results)
        self.fetches = 0
        self.gate = gate
        self.closed = False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_ScriptedPool]:
        yield self

    async def fetch(self, sql: str) -> list[dict[str, str]]:
        self.fetches += 1
        if self.gate is not None:
            await self.gate.wait()
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return [{"name": name} for name in result]

    # /health/db 의 check_health 가 쓰는 것들
    async def fetchrow(self, sql: str) -> dict[str, str]:
        return {"db": "test", "version": "PostgreSQL 16.0 on test"}

    def get_size(self) -> int:
        return 1

    def get_idle_size(self) -> int:
        return 1

    async def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


SETTINGS = ReasonSettings(api_key="test-key")


async def test_failed_load_is_retried_after_the_interval() -> None:
    """실패한 뒤 간격 안에서는 다시 읽지 않고, 간격이 지나면 다시 읽어 켭니다."""
    clock = _Clock()
    pool = _ScriptedPool(asyncpg.InterfaceError("connection reset"), ["두부", "대파"])
    runtime = ReasonRuntime(SETTINGS, clock=clock)

    assert await runtime.try_enable(pool) is False
    assert await runtime.try_enable(pool) is False
    assert pool.fetches == 1, "재시도 간격 안에서 사전을 다시 읽었습니다"

    clock.now += RETRY_SECONDS
    assert await runtime.try_enable(pool) is True
    assert runtime.vocabulary == frozenset({"두부", "대파"})
    assert await runtime.try_enable(pool) is True
    assert pool.fetches == 2, "켜진 뒤에도 사전을 다시 읽었습니다"
    await runtime.aclose()


async def test_empty_vocabulary_is_retried_too() -> None:
    """0행(카탈로그 복원 전)도 켜지 않고, 복원된 뒤 간격이 지나면 켭니다."""
    clock = _Clock()
    pool = _ScriptedPool([], ["두부"])
    runtime = ReasonRuntime(SETTINGS, clock=clock)

    assert await runtime.try_enable(pool) is False
    clock.now += RETRY_SECONDS
    assert await runtime.try_enable(pool) is True
    await runtime.aclose()


async def test_concurrent_requests_load_once_without_waiting() -> None:
    """동시에 들어온 요청이 한꺼번에 사전을 읽지 않습니다. 조회는 한 번뿐입니다.

    조회가 진행 중일 때 들어온 요청은 기다리지 않고 규칙 문구로 답합니다(False). 부가 기능 때문에
    요청 지연을 늘리지 않기 위해서입니다. 조회가 끝나면 다음 요청부터 켜진 상태를 씁니다.
    """
    gate = asyncio.Event()
    pool = _ScriptedPool(["두부"], gate=gate)
    runtime = ReasonRuntime(SETTINGS, clock=_Clock())

    attempts = [asyncio.create_task(runtime.try_enable(pool)) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*attempts)

    assert results == [True, False, False, False, False]
    assert pool.fetches == 1
    assert await runtime.try_enable(pool) is True
    assert pool.fetches == 1
    await runtime.aclose()


async def test_without_key_or_pool_never_loads() -> None:
    """키가 없거나 DB 가 없으면 사전을 읽지 않고 규칙 문구로 둡니다."""
    pool = _ScriptedPool(["두부"])

    assert await ReasonRuntime(None).try_enable(pool) is False
    assert await ReasonRuntime(SETTINGS).try_enable(None) is False
    assert pool.fetches == 0


async def test_health_db_reports_and_recovers_reason_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """기동 때 꺼졌어도 준비 상태 프로브가 다시 켜고, `/health/db` 가 그 상태를 보여 줍니다.

    트래픽이 없어도 프로브(10초마다)가 복구를 이어 갑니다. LLM 은 부가 기능이라 `ok` 에는 넣지 않습니다.
    """
    import serving.app as app_module
    from serving.app import create_app

    pool = _ScriptedPool(asyncpg.InterfaceError("connection reset"), ["두부"])

    async def fake_create_pool(settings: Settings) -> _ScriptedPool:
        return pool

    monkeypatch.setattr(app_module, "create_pool", fake_create_pool)
    # 간격을 0 으로 두어 다음 호출이 바로 재시도하게 합니다. 간격 자체는 위 단위 테스트가 봅니다.
    monkeypatch.setattr(reason_runtime, "RETRY_SECONDS", 0.0)
    app = create_app(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            shutdown_delay_seconds=0,
            database_url="postgresql://user:pass@localhost/db",
            openrouter_api_key="test-key",
        )
    )
    async with app.router.lifespan_context(app):
        assert not app.state.reason_runtime.enabled, "기동 때 조회가 실패했으니 꺼져 있어야 합니다"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body: dict[str, Any] = (await client.get("/health/db")).json()

    assert body["ok"] is True
    assert body["reason_llm"] == "on"
    assert pool.fetches == 2
