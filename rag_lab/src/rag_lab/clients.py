"""LLM / 임베딩 클라이언트.

Protocol 로 감싸 두었기 때문에 테스트에서는 가짜 구현을 끼워 넣어 API 호출 없이
그래프 전체를 돌릴 수 있습니다.

구현체는 공급자에 묶여 있지 않습니다. OpenAI, OpenRouter, Together, vLLM 은 전부
OpenAI 호환 API 라 `base_url` 만 바꾸면 같은 SDK 로 붙습니다. 어디에 붙을지는
`providers.py` 의 레지스트리와 `.env` 의 `LLM_PROVIDER` 가 정합니다.

**채팅과 임베딩은 서로 다른 곳에 붙을 수 있습니다.** OpenRouter 와 Anthropic 은 임베딩
엔드포인트가 없어서, 채팅만 거기서 받고 임베딩은 OpenAI 나 self-hosted 로 보내는 조합이
흔합니다. 그래서 두 클라이언트가 각자 공급자를 해석합니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI

from rag_lab.config import Settings, get_settings
from rag_lab.providers import Provider, ensure_embeddings, get_provider, resolve_base_url


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


def _build_client(*, api_key: str, provider: Provider, base_url: str | None) -> AsyncOpenAI:
    """OpenAI 호환 클라이언트 하나를 만듭니다."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=dict(provider.default_headers) or None,
    )


class LlmEmbeddingClient:
    """임베딩 어댑터. 공급자는 `.env` 가 정합니다."""

    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings or get_settings()
        self.provider = get_provider(self._settings.embedding_provider_name)
        # 임베딩을 줄 수 없는 공급자면 호출 전에 막습니다. 안 그러면 실행 중 404 가 납니다.
        ensure_embeddings(self.provider)
        self._client = client or _build_client(
            api_key=self._settings.require_embedding_api_key(),
            provider=self.provider,
            # 채팅 주소를 물려받는 것은 아무것도 따로 지정하지 않았을 때뿐입니다.
            # 임베딩만 다른 곳으로 보내려는데 채팅 주소가 딸려 오면, 요청과 키가
            # 엉뚱한 서버로 갑니다.
            base_url=resolve_base_url(
                self.provider,
                self._settings.llm_embedding_base_url
                or (self._settings.llm_base_url if self._settings.embedding_inherits_chat else ""),
            ),
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """입력 순서를 유지한 채 임베딩을 돌려줍니다."""
        if not texts:
            return []
        response = await self._client.embeddings.create(
            model=self._settings.llm_embedding_model,
            input=list(texts),
            dimensions=self._settings.embedding_dim,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        self._check_dimension(vectors)
        return vectors

    def _check_dimension(self, vectors: list[list[float]]) -> None:
        """차원이 DB 컬럼과 다르면 여기서 막습니다.

        `recipe/product/ingredient.embedding` 이 `VECTOR(1536)` 입니다. 차원이 다른
        모델(BAAI/bge-m3 는 1024)로 바꾸면 검색이 조용히 0건이 되는 게 아니라
        INSERT 시점에 터지는데, 그때는 원인이 멀리 있습니다. 여기서 바로 알립니다.

        `dimensions` 인자를 무시하는 공급자가 있어서 응답으로 확인합니다.
        """
        expected = self._settings.embedding_dim
        actual = len(vectors[0]) if vectors else expected
        if actual != expected:
            raise RuntimeError(
                f"임베딩 차원이 {actual} 인데 EMBEDDING_DIM 은 {expected} 입니다. "
                f"모델({self._settings.llm_embedding_model})을 바꿨다면 embedding 컬럼 마이그레이션이 함께 필요합니다."
            )


class LlmChatClient:
    """chat completions 어댑터. 공급자는 `.env` 가 정합니다."""

    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings or get_settings()
        self.provider = get_provider(self._settings.llm_provider)
        self._client = client or _build_client(
            api_key=self._settings.require_llm_api_key(),
            provider=self.provider,
            base_url=resolve_base_url(self.provider, self._settings.llm_base_url),
        )

    async def complete(self, system: str, user: str) -> str:
        """한 번 호출하고 텍스트만 꺼냅니다."""
        response = await self._client.chat.completions.create(
            model=self._settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
