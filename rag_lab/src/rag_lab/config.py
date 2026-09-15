"""rag_lab 설정 로딩. 값은 rag_lab/.env 에서만 읽습니다."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/rag_lab/config.py -> rag_lab/
PACKAGE_DIR = Path(__file__).resolve().parents[2]

DistanceMetric = Literal["cosine", "l2", "inner_product"]

# pgvector 거리 연산자. 값이 작을수록 가깝습니다.
DISTANCE_OPERATORS: dict[str, str] = {
    "cosine": "<=>",
    "l2": "<->",
    "inner_product": "<#>",
}


class Settings(BaseSettings):
    """rag_lab 이 쓰는 환경변수 전체."""

    model_config = SettingsConfigDict(
        env_file=PACKAGE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: SecretStr | None = None
    database_url_direct: SecretStr | None = None
    neon_branch: str = ""

    # --- LLM 공급자 -----------------------------------------------------------
    #
    # 공급자를 `.env` 한 줄로 바꿀 수 있어야 합니다. RAG 실험은 모델을 갈아끼우며
    # 비교하는 것이 목적인데, `OPENAI_*` 로 이름을 박아 두면 코드까지 고쳐야 합니다.
    #
    # 예전 이름(`OPENAI_*`)도 계속 읽습니다. 팀원 로컬 `.env` 가 조용히 깨지지 않게
    # 두는 것이고, 새로 쓰는 값은 `LLM_*` 입니다.
    llm_provider: str = Field(default="openai", validation_alias=AliasChoices("llm_provider"))
    llm_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("llm_api_key", "openai_api_key"))
    llm_model: str = Field(default="gpt-4.1-mini", validation_alias=AliasChoices("llm_model", "openai_chat_model"))
    # 레지스트리에 없는 OpenAI 호환 엔드포인트를 붙일 때만 씁니다(LLM_PROVIDER=custom).
    llm_base_url: str = ""

    # 임베딩은 채팅과 다른 곳에서 받을 수 있어야 합니다.
    # OpenRouter 와 Anthropic 은 임베딩 엔드포인트가 없습니다. 비워 두면 채팅 쪽 설정을 씁니다.
    llm_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices("llm_embedding_model", "openai_embedding_model"),
    )
    llm_embedding_provider: str = ""
    llm_embedding_api_key: SecretStr | None = None
    llm_embedding_base_url: str = ""

    # recipe/product/ingredient.embedding 이 VECTOR(1536) 입니다.
    # 차원이 다른 모델(BAAI/bge-m3 는 1024)로 바꾸려면 마이그레이션이 함께 필요합니다.
    embedding_dim: int = 1536

    top_k: int = Field(default=5, ge=1, le=100)
    score_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    distance_metric: DistanceMetric = "cosine"

    experiment_owner: str = ""

    @property
    def distance_operator(self) -> str:
        """설정된 거리 지표에 해당하는 pgvector 연산자."""
        return DISTANCE_OPERATORS[self.distance_metric]

    @property
    def experiments_dir(self) -> Path:
        """실험 폴더 루트. 각자 폴더는 이 아래 github id 로 만듭니다."""
        return PACKAGE_DIR / "experiments"

    @property
    def owner_dir(self) -> Path:
        """EXPERIMENT_OWNER 가 지정되어 있으면 그 폴더, 아니면 실험 루트."""
        return self.experiments_dir / self.experiment_owner if self.experiment_owner else self.experiments_dir

    def require_database_url(self, *, direct: bool = False) -> str:
        """DB URL 을 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        url = self.database_url_direct if direct else self.database_url
        if url is None or not url.get_secret_value().strip():
            key = "DATABASE_URL_DIRECT" if direct else "DATABASE_URL"
            raise RuntimeError(f"{key} 가 비어 있습니다. rag_lab/.env 를 확인하세요.")
        return url.get_secret_value()

    @property
    def embedding_provider_name(self) -> str:
        """임베딩 공급자. 따로 지정하지 않았으면 채팅 쪽을 그대로 씁니다."""
        return self.llm_embedding_provider.strip() or self.llm_provider

    def require_llm_api_key(self) -> str:
        """LLM 키를 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        return self._require_secret(self.llm_api_key, "LLM_API_KEY")

    @property
    def embedding_inherits_chat(self) -> bool:
        """임베딩이 채팅 설정을 그대로 물려받는 상태인지.

        공급자든 주소든 하나라도 따로 지정했으면 물려받지 않습니다. 같은 공급자
        이름이어도 주소가 다르면 다른 서버라, 이름만 보고 판단하면 안 됩니다.
        """
        return not (self.llm_embedding_provider.strip() or self.llm_embedding_base_url.strip())

    def require_embedding_api_key(self) -> str:
        """임베딩용 키.

        **임베딩을 따로 지정했으면 키도 따로 받습니다.** 채팅 키를 물려주면 그 키가
        다른 회사 엔드포인트로 그대로 나갑니다(예: OpenAI 키를 self-hosted 주소로).
        한 번 나간 키는 회수할 수 없습니다. 편의보다 이쪽이 먼저입니다.

        아무것도 따로 지정하지 않았을 때만 채팅 키를 씁니다. 그때는 같은 서버입니다.
        """
        if self.llm_embedding_api_key is not None and self.llm_embedding_api_key.get_secret_value().strip():
            return self.llm_embedding_api_key.get_secret_value()
        if not self.embedding_inherits_chat:
            raise RuntimeError(
                "LLM_EMBEDDING_API_KEY 가 비어 있습니다. "
                "임베딩 공급자나 주소를 따로 지정했으면 키도 따로 넣어야 합니다. "
                "채팅 키를 다른 엔드포인트로 보내지 않습니다."
            )
        return self._require_secret(self.llm_api_key, "LLM_API_KEY")

    @staticmethod
    def _require_secret(value: SecretStr | None, key: str) -> str:
        """비밀값을 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        if value is None or not value.get_secret_value().strip():
            raise RuntimeError(f"{key} 가 비어 있습니다. rag_lab/.env 를 확인하세요.")
        return value.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 .env 를 읽어 재사용합니다."""
    return Settings()
