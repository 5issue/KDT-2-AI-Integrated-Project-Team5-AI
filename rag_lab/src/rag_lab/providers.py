"""LLM 공급자 레지스트리.

`OPENAI_*` 로 이름을 박아 두면 공급자를 바꿀 때 코드까지 고쳐야 합니다. RAG 실험은
모델을 갈아끼우며 비교하는 것이 목적이라, 공급자도 `.env` 한 줄로 바뀌어야 합니다.

## 왜 이렇게 되나

OpenAI, OpenRouter, Together, Groq, DeepInfra, vLLM 은 전부 **OpenAI 호환 API** 입니다.
엔드포인트 모양이 같고 `base_url` 만 다릅니다. 그래서 공급자 하나를
`(base_url, 헤더, 임베딩 지원 여부)` 로만 적어 두면 `AsyncOpenAI` 클라이언트 하나로
전부 붙습니다. SDK 를 공급자마다 따로 둘 이유가 없습니다.

## 임베딩을 따로 두는 이유

**채팅 공급자가 임베딩까지 준다는 보장이 없습니다.** OpenRouter 와 Anthropic 은
임베딩 엔드포인트가 없습니다. `LLM_PROVIDER=openrouter` 로 두고 임베딩까지 거기서
받으려 하면 404 가 납니다.

그래서 임베딩 쪽은 별도로 덮어쓸 수 있게 두었습니다(`LLM_EMBEDDING_PROVIDER` 등).
지정하지 않으면 채팅 쪽 설정을 그대로 씁니다. 흔한 조합은 이렇습니다.

    채팅   OpenRouter (anthropic/claude-sonnet-4 등 여러 모델을 한 키로)
    임베딩 OpenAI 또는 self-hosted TEI (BAAI/bge-m3)

## 새 공급자 추가

OpenAI 호환이면 `PROVIDERS` 에 한 줄이면 됩니다. 호환되지 않는 API(예: Anthropic
Messages API 원형)를 쓰려면 `clients.py` 에 어댑터를 하나 더 만들어야 합니다.
`custom` 은 `LLM_BASE_URL` 을 직접 받으므로, 레지스트리를 건드리지 않고
self-hosted 엔드포인트를 붙일 때 씁니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Provider:
    """OpenAI 호환 엔드포인트 하나."""

    name: str
    # None 이면 SDK 기본값(OpenAI). 그 외에는 이 주소로 붙습니다.
    base_url: str | None
    # 임베딩 엔드포인트가 있는지. 없는데 쓰려고 하면 미리 막습니다.
    supports_embeddings: bool
    # 공급자가 요구하거나 권장하는 추가 헤더.
    default_headers: dict[str, str] = field(default_factory=dict)
    note: str = ""


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        name="openai",
        base_url=None,
        supports_embeddings=True,
        note="모델명에 접두사를 붙이지 않습니다. gpt-4.1-mini / text-embedding-3-small",
    ),
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        supports_embeddings=False,
        # OpenRouter 는 이 두 헤더로 호출자를 식별합니다. 없어도 동작하지만 붙이는 편이 낫습니다.
        default_headers={
            "HTTP-Referer": "https://github.com/5issue/KDT-2-AI-Integrated-Project-Team5-AI",
            "X-Title": "team5-ai-rag-lab",
        },
        note="모델명에 공급자 접두사가 필요합니다. openai/gpt-4.1-mini, anthropic/claude-sonnet-4",
    ),
    "anthropic": Provider(
        name="anthropic",
        # OpenAI SDK 호환 계층. Messages API 원형과 달리 일부 기능이 빠집니다.
        base_url="https://api.anthropic.com/v1/",
        supports_embeddings=False,
        note="임베딩 API 가 없습니다. 임베딩은 다른 공급자로 지정하세요",
    ),
    "custom": Provider(
        name="custom",
        # LLM_BASE_URL / LLM_EMBEDDING_BASE_URL 에서 받습니다.
        base_url=None,
        supports_embeddings=True,
        note="self-hosted(vLLM, TEI) 또는 레지스트리에 없는 OpenAI 호환 서비스",
    ),
}

PROVIDER_NAMES = tuple(PROVIDERS)


class ProviderError(RuntimeError):
    """공급자 설정이 잘못되었을 때. 자격증명은 담지 않습니다."""


def get_provider(name: str) -> Provider:
    """이름으로 공급자를 찾습니다. 오타는 여기서 걸립니다."""
    try:
        return PROVIDERS[name.strip().lower()]
    except KeyError as exc:
        raise ProviderError(f"모르는 공급자입니다: {name!r} (가능한 값: {', '.join(PROVIDER_NAMES)})") from exc


def resolve_base_url(provider: Provider, override: str | None) -> str | None:
    """붙을 주소를 정합니다. `custom` 은 주소를 반드시 받아야 합니다."""
    if override and override.strip():
        return override.strip()
    if provider.name == "custom":
        raise ProviderError("LLM_PROVIDER=custom 이면 LLM_BASE_URL 을 채워야 합니다.")
    return provider.base_url


def ensure_embeddings(provider: Provider) -> None:
    """임베딩을 줄 수 없는 공급자면 호출 전에 막습니다.

    OpenRouter 로 두고 임베딩까지 받으려다 404 를 보는 일이 흔합니다.
    어디를 고쳐야 하는지 메시지에 적어 둡니다.
    """
    if provider.supports_embeddings:
        return
    raise ProviderError(
        f"{provider.name} 에는 임베딩 엔드포인트가 없습니다. "
        "LLM_EMBEDDING_PROVIDER 로 임베딩만 다른 곳을 쓰세요 (예: openai)."
    )
