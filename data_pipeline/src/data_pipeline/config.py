"""data_pipeline 설정 로딩. 값은 data_pipeline/.env 에서만 읽습니다."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/data_pipeline/config.py -> data_pipeline/
PACKAGE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """data_pipeline 이 쓰는 환경변수 전체."""

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
    openai_batch_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    batch_completion_window: str = "24h"

    # recipe.source_type 에 들어갈 값. (source_type, source_recipe_id) 가 레시피 자연키입니다.
    recipe_source_type: str = "LLM_PARSE"

    batch_max_requests: int = Field(default=40_000, ge=1, le=50_000)
    copy_chunk_size: int = Field(default=5_000, ge=1)
    dry_run: bool = False

    @property
    def raw_dir(self) -> Path:
        """원본 데이터 디렉터리."""
        return PACKAGE_DIR / "data" / "raw"

    @property
    def batch_dir(self) -> Path:
        """Batch API 입출력 JSONL 을 보관하는 디렉터리."""
        return PACKAGE_DIR / "data" / "batch"

    @property
    def package_samples(self) -> Path:
        """형식 확인용 샘플 JSONL 디렉터리."""
        return PACKAGE_DIR / "samples"

    @property
    def sql_dir(self) -> Path:
        """bulk insert 용 .sql 파일 디렉터리."""
        return PACKAGE_DIR / "sql"

    def require_database_url(self, *, direct: bool = False) -> str:
        """DB URL 을 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        url = self.database_url_direct if direct else self.database_url
        if url is None or not url.get_secret_value().strip():
            key = "DATABASE_URL_DIRECT" if direct else "DATABASE_URL"
            raise RuntimeError(f"{key} 가 비어 있습니다. data_pipeline/.env 를 확인하세요.")
        return url.get_secret_value()

    def require_openai_api_key(self) -> str:
        """OpenAI 키를 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY 가 비어 있습니다. data_pipeline/.env 를 확인하세요.")
        return self.openai_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 .env 를 읽어 재사용합니다."""
    return Settings()
