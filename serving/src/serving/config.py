"""serving 설정 로딩. 값은 serving/.env 에서만 읽습니다."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# src/serving/config.py -> serving/
PACKAGE_DIR = Path(__file__).resolve().parents[2]

Environment = Literal["local", "dev", "prod"]


class Settings(BaseSettings):
    """serving 이 쓰는 환경변수 전체."""

    model_config = SettingsConfigDict(
        env_file=PACKAGE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: SecretStr | None = None
    neon_branch: str = ""

    db_pool_min_size: int = Field(default=1, ge=0)
    db_pool_max_size: int = Field(default=10, ge=1)
    db_pool_timeout: float = Field(default=10.0, gt=0)
    db_command_timeout: float = Field(default=5.0, gt=0)

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # 종료 유예(초). SIGTERM 후 ALB 가 이 파드를 트래픽에서 제외할 때까지 기다려
    # 502 를 막습니다 (전파에 보통 2~5초). 로컬/테스트는 0 으로 끕니다.
    shutdown_delay_seconds: float = Field(default=5.0, ge=0)

    # /api/v1 분당 요청 한도. 0 이면 비활성. 추천 경로는 별도(더 낮은) 한도.
    # 프로세스별 카운터라 워커 수만큼 배수가 됩니다.
    rate_limit_per_minute: int = Field(default=60, ge=0)
    rate_limit_reco_per_minute: int = Field(default=10, ge=0)

    cors_allow_origins: Annotated[tuple[str, ...], NoDecode] = ()
    environment: Environment = "local"

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        """쉼표로 구분된 환경변수 문자열을 튜플로 바꿉니다."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @property
    def docs_url(self) -> str | None:
        """local 이 아니면 문서 페이지를 닫습니다. 스키마 노출을 줄입니다."""
        # FE 가 연동 전 dev 데모에서 계약을 확인할 수 있게 prod 에서만 닫습니다.
        return "/docs" if self.environment in ("local", "dev") else None

    def require_database_url(self) -> str:
        """DB URL 을 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        if self.database_url is None or not self.database_url.get_secret_value().strip():
            raise RuntimeError("DATABASE_URL 이 비어 있습니다. serving/.env 를 확인하세요.")
        return self.database_url.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 .env 를 읽어 재사용합니다."""
    return Settings()
