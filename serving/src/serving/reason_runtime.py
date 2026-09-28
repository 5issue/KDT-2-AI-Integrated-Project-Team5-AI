"""추천 이유 LLM 을 켤지 정하고, 꺼져 있으면 스스로 다시 켭니다.

LLM 은 **재료 사전**(`SELECT name FROM ingredient`)이 있어야 켭니다. 사전은 LLM 이 지어낸 재료를 거르는
검사(`환각_재료`)의 입력이라, 없으면 그 검사가 빠진 문구가 나갑니다. 그래서 아래 경우는 규칙 문구로 둡니다.

- 키가 없음 (`OPENROUTER_API_KEY`)
- DB 가 없음 (이때 my-recipes 는 어차피 503)
- 사전 조회 실패 또는 0행 (카탈로그 복원 전 DB)

조회 실패와 0행은 일시적일 수 있습니다. 기동 때 한 번 실패로 파드가 사는 동안 LLM 이 꺼져 있으면, 응답은
200 이라 운영자가 모르는 채 규칙 문구만 나갑니다. 그래서 요청이나 준비 상태 프로브(`/health/db`)가 올 때
``RETRY_SECONDS`` 에 한 번만 다시 읽어 봅니다. 동시에 들어온 요청이 한꺼번에 조회하지 않게 잠금을 둡니다.
켜졌는지는 `/health/db` 의 `reason_llm` 으로 보입니다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

import asyncpg
import httpx

from rag_lab.reason_service import OpenRouterReasonClient, ReasonSettings
from rag_lab.reason_service.service import ReasonClient

logger = logging.getLogger("serving")

# 꺼져 있을 때 사전을 다시 읽는 최소 간격(초). 준비 상태 프로브가 10초마다 와도 조회는 분당 한 번입니다.
RETRY_SECONDS = 60.0

# 요청·프로브 경로에서 사전을 다시 읽을 때의 상한(초). 그 요청이 DB 명령 제한(5초)까지 기다리지 않게 합니다.
# 넘으면 이번에는 규칙 문구로 답하고 다음 간격에 다시 봅니다. 준비 상태 프로브의 기본 응답 제한도 1초입니다.
# 기동 때 첫 조회는 이 상한을 쓰지 않습니다(깨어나는 중인 DB 를 기다려도 되는 자리).
RETRY_TIMEOUT_SECONDS = 1.0

# 사전 조회에서 잡는 예외. 추천 이유는 부가 기능이라 이 조회 실패가 앱 기동이나 요청을 막으면 안 됩니다.
# asyncpg 의 InterfaceError 와 InternalClientError 는 PostgresError 를 상속하지 않아 따로 적습니다.
_VOCABULARY_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, asyncpg.InternalClientError, OSError, TimeoutError)


class _Pool(Protocol):
    """``acquire()`` 만 쓰는 풀. asyncpg 풀과 테스트의 가짜 풀이 모두 맞습니다."""

    def acquire(self) -> AbstractAsyncContextManager[Any]: ...


async def load_ingredient_vocabulary(pool: _Pool) -> frozenset[str] | None:
    """재료 사전을 읽습니다. 읽지 못하거나 비어 있으면 None 입니다."""
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT name FROM ingredient")
    except _VOCABULARY_ERRORS as exc:
        # 예외 메시지에 호스트가 들어갈 수 있어 타입만 남깁니다.
        logger.warning("재료 사전을 읽지 못해 추천 이유는 규칙 기반 문구만 씁니다: %s", type(exc).__name__)
        return None
    vocabulary = frozenset(str(row["name"]) for row in rows)
    if not vocabulary:
        # 사전 없이 LLM 을 켜면 지어낸 재료 검사가 항상 통과합니다. 카탈로그 복원 전 DB 가 이 경우입니다.
        logger.warning("재료 사전이 비어 있어(0종) 추천 이유는 규칙 기반 문구만 씁니다")
        return None
    return vocabulary


class ReasonRuntime:
    """추천 이유 LLM 클라이언트와 재료 사전. 앱 수명 동안 하나를 ``app.state.reason_runtime`` 에 둡니다."""

    def __init__(self, settings: ReasonSettings | None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_attempt = 0.0
        self._http: httpx.AsyncClient | None = None
        self.client: ReasonClient | None = None
        self.vocabulary: frozenset[str] = frozenset()

    @classmethod
    def enabled_with(cls, client: ReasonClient, vocabulary: frozenset[str]) -> ReasonRuntime:
        """이미 준비된 클라이언트와 사전으로 켠 상태를 만듭니다. 테스트와 시연 스크립트가 씁니다."""
        runtime = cls(None)
        runtime.client = client
        runtime.vocabulary = vocabulary
        return runtime

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def try_enable(self, pool: _Pool | None, *, timeout: float | None = RETRY_TIMEOUT_SECONDS) -> bool:
        """꺼져 있으면 사전을 읽어 켭니다. 켜져 있거나 켜면 True. 재시도 간격 안이면 읽지 않습니다.

        ``timeout`` 은 사전 조회의 상한입니다. 요청·프로브 경로는 기본값(``RETRY_TIMEOUT_SECONDS``)을,
        기동 때 lifespan 은 None(DB 명령 제한만 적용)을 씁니다.
        """
        if self.client is not None:
            return True
        if self._settings is None or pool is None or self._clock() < self._next_attempt:
            return False
        async with self._lock:
            # 잠금을 기다리는 동안 다른 요청이 이미 켰거나 시도했을 수 있습니다.
            if self.client is not None:
                return True
            if self._clock() < self._next_attempt:
                return False
            self._next_attempt = self._clock() + RETRY_SECONDS
            try:
                vocabulary = await asyncio.wait_for(load_ingredient_vocabulary(pool), timeout)
            except TimeoutError:
                logger.warning("재료 사전 조회가 %.1f초를 넘어 이번에는 규칙 기반 문구만 씁니다", timeout)
                return False
            if vocabulary is None:
                return False
            self.vocabulary = vocabulary
            # httpx.AsyncClient 는 켜진 동안 하나만 씁니다. 키는 클라이언트 안에만 있고 로그에 남지 않습니다.
            self._http = httpx.AsyncClient()
            self.client = OpenRouterReasonClient(self._http, self._settings)
            logger.info("추천 이유 LLM 생성 사용: model=%s, 사전 %d종", self._settings.model, len(vocabulary))
            return True

    async def aclose(self) -> None:
        """HTTP 클라이언트를 닫고 규칙 문구 상태로 돌아갑니다."""
        if self._http is not None:
            await self._http.aclose()
        self._http = None
        self.client = None
