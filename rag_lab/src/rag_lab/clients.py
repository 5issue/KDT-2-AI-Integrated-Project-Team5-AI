"""LLM / 임베딩 클라이언트.

Protocol 로 감싸 두었기 때문에 테스트에서는 가짜 구현을 끼워 넣어 API 호출 없이
그래프 전체를 돌릴 수 있습니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI

from rag_lab.config import Settings, get_settings


@runtime_checkable
class EmbeddingClient(Protocol):
    """문장을 벡터로 바꾸는 것."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """여러 문장을 한 번에 임베딩합니다."""
        ...


@runtime_checkable
class ChatClient(Protocol):
    """근거를 받아 답을 쓰는 것."""

    async def complete(self, system: str, user: str) -> str:
        """시스템/사용자 메시지로 한 번 호출합니다."""
        ...


class OpenAIEmbeddingClient:
    """OpenAI 임베딩 API 어댑터."""

    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client or AsyncOpenAI(api_key=self._settings.require_openai_api_key())

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """입력 순서를 유지한 채 임베딩을 돌려줍니다."""
        if not texts:
            return []
        response = await self._client.embeddings.create(
            model=self._settings.openai_embedding_model,
            input=list(texts),
            dimensions=self._settings.embedding_dim,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


class OpenAIChatClient:
    """OpenAI chat completions 어댑터."""

    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client or AsyncOpenAI(api_key=self._settings.require_openai_api_key())

    async def complete(self, system: str, user: str) -> str:
        """한 번 호출하고 텍스트만 꺼냅니다."""
        response = await self._client.chat.completions.create(
            model=self._settings.openai_chat_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
