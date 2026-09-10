"""rag_lab 설정 로딩. 값은 rag_lab/.env 에서만 읽습니다."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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

    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
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

    def require_openai_api_key(self) -> str:
        """OpenAI 키를 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY 가 비어 있습니다. rag_lab/.env 를 확인하세요.")
        return self.openai_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 .env 를 읽어 재사용합니다."""
    return Settings()
