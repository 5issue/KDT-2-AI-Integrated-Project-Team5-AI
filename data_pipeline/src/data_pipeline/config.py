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

    # 1단계 프롬프트에 넣을 샘플 행 수.
    profile_sample_rows: int = Field(default=5, ge=1, le=50)
    # 2단계에서 데이터셋당 처리할 엔티티 상한. 비용을 조절하며 실험할 때 씁니다. 0 이면 무제한.
    extract_max_entities: int = Field(default=0, ge=0)
    # 3단계에서 한 요청에 묶을 재료명 개수. 마스터 목록 토큰을 여러 이름이 나눠 씁니다.
    match_chunk_size: int = Field(default=25, ge=1, le=200)
    # 이 확신도 미만의 매칭은 채택하지 않고 미매칭으로 보고합니다.
    match_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    # recipe.source_type 기본값. 비워두면 데이터셋 이름을 씁니다.
    recipe_source_type: str = ""
    # 용어 기준표 원본 경로. 비워두면 domain.TERMINOLOGY_GUIDE 를 씁니다.
    terminology_path: Path | None = None

    batch_max_requests: int = Field(default=40_000, ge=1, le=50_000)
    # 입력 파일 하나에 담을 토큰 상한. OpenAI 는 조직 단위로 "대기 중인 토큰" 한도를 두는데
    # (gpt-4.1-mini 기준 200만) 한 번에 넘기면 배치가 몇 초 만에 token_limit_exceeded 로
    # 죽습니다. 파일을 나눠 순차 제출하려고 둡니다.
    batch_max_tokens: int = Field(default=1_000_000, ge=1_000)
    copy_chunk_size: int = Field(default=5_000, ge=1)
    dry_run: bool = False

    @property
    def raw_dir(self) -> Path:
        """원본 데이터 디렉터리."""
        return PACKAGE_DIR / "data" / "raw"

    def stage_dir(self, stage: str) -> Path:
        """단계별 산출물 디렉터리. 단계마다 requests/ results/ 를 따로 둡니다."""
        return PACKAGE_DIR / "data" / stage

    @property
    def artifacts_dir(self) -> Path:
        """단계별 중간 산출물(프로파일, 추출 레코드, 매칭 결과)을 모으는 곳."""
        return PACKAGE_DIR / "data" / "artifacts"

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
