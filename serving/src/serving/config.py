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
        return "/docs" if self.environment == "local" else None

    def require_database_url(self) -> str:
        """DB URL 을 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        if self.database_url is None or not self.database_url.get_secret_value().strip():
            raise RuntimeError("DATABASE_URL 이 비어 있습니다. serving/.env 를 확인하세요.")
        return self.database_url.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 .env 를 읽어 재사용합니다."""
    return Settings()
