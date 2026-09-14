"""공급자 레지스트리와 클라이언트 조립 검증.

API 키 없이 도는 테스트입니다. 여기서 걸리는 실수가 실제로 비용과 시간을 씁니다.
"""

from __future__ import annotations

import pytest

from rag_lab.clients import LlmChatClient, LlmEmbeddingClient
from rag_lab.config import Settings
from rag_lab.providers import PROVIDER_NAMES, ProviderError, ensure_embeddings, get_provider, resolve_base_url


def settings(**overrides: object) -> Settings:
    """.env 를 읽지 않는 설정 객체. 테스트가 로컬 환경에 좌우되지 않게 합니다."""
    base: dict[str, object] = {"llm_api_key": "test-key", "_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_unknown_provider_names_the_alternatives() -> None:
    """오타로 죽을 때 뭐가 가능한지는 알려 줘야 합니다."""
    with pytest.raises(ProviderError, match="모르는 공급자입니다"):
        get_provider("opnerouter")


def test_provider_name_is_case_insensitive() -> None:
    """`.env` 에 대문자로 적는 사람이 있습니다."""
    assert get_provider("OpenRouter").name == "openrouter"


def test_openrouter_has_no_embeddings() -> None:
    """임베딩을 못 주는 공급자로 임베딩을 받으려다 실행 중 404 를 보는 일이 흔합니다."""
    with pytest.raises(ProviderError, match="임베딩 엔드포인트가 없습니다"):
        ensure_embeddings(get_provider("openrouter"))

    with pytest.raises(ProviderError, match="임베딩 엔드포인트가 없습니다"):
        ensure_embeddings(get_provider("anthropic"))


def test_custom_provider_needs_an_address() -> None:
    """self-hosted 인데 주소가 없으면 조용히 OpenAI 로 가면 안 됩니다."""
    with pytest.raises(ProviderError, match="LLM_BASE_URL"):
        resolve_base_url(get_provider("custom"), None)

    assert resolve_base_url(get_provider("custom"), "http://localhost:8000/v1") == "http://localhost:8000/v1"


def test_openai_uses_the_sdk_default_address() -> None:
    """OpenAI 는 base_url 을 넘기지 않습니다."""
    assert resolve_base_url(get_provider("openai"), None) is None


def test_chat_client_points_at_the_configured_provider() -> None:
    """`.env` 한 줄로 붙는 곳이 바뀌어야 합니다."""
    client = LlmChatClient(settings(llm_provider="openrouter"))

    assert client.provider.name == "openrouter"
    assert "openrouter.ai" in str(client._client.base_url)


def test_embedding_falls_back_to_the_chat_provider() -> None:
    """따로 지정하지 않았으면 채팅 쪽을 그대로 씁니다."""
    client = LlmEmbeddingClient(settings(llm_provider="openai"))

    assert client.provider.name == "openai"


def test_embedding_provider_can_differ_from_chat() -> None:
    """채팅은 OpenRouter, 임베딩은 OpenAI 조합이 흔합니다."""
    config = settings(llm_provider="openrouter", llm_embedding_provider="openai")

    assert LlmChatClient(config).provider.name == "openrouter"
    assert LlmEmbeddingClient(config).provider.name == "openai"


def test_embedding_client_refuses_a_provider_without_embeddings() -> None:
    """조립 시점에 막습니다. 실험을 한참 돌린 뒤 404 를 보면 늦습니다."""
    with pytest.raises(ProviderError, match="임베딩 엔드포인트가 없습니다"):
        LlmEmbeddingClient(settings(llm_provider="openrouter"))


def test_legacy_openai_names_still_load() -> None:
    """팀원 로컬 `.env` 가 조용히 깨지지 않게 예전 이름도 읽습니다."""
    legacy = {
        "OPENAI_API_KEY": "legacy-key",
        "OPENAI_CHAT_MODEL": "gpt-4o-mini",
        "OPENAI_EMBEDDING_MODEL": "text-embedding-3-large",
    }
    config = Settings(_env_file=None, **legacy)  # type: ignore[arg-type]

    assert config.require_llm_api_key() == "legacy-key"
    assert config.llm_model == "gpt-4o-mini"
    assert config.llm_embedding_model == "text-embedding-3-large"


def test_missing_key_fails_without_leaking() -> None:
    """키가 없을 때 값이 메시지에 섞이면 안 됩니다."""
    with pytest.raises(RuntimeError, match="LLM_API_KEY 가 비어 있습니다"):
        Settings(_env_file=None).require_llm_api_key()  # type: ignore[arg-type]


def test_registry_entries_are_well_formed() -> None:
    """공급자를 추가할 때 빠뜨리기 쉬운 것들."""
    for name in PROVIDER_NAMES:
        provider = get_provider(name)
        assert provider.name == name
        assert provider.note, f"{name}: 모델명 표기 규칙을 note 에 적어 주세요"
