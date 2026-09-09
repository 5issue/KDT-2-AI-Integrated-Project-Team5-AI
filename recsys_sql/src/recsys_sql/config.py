"""recsys_sql 설정 로딩. 값은 recsys_sql/.env 에서만 읽습니다."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# src/recsys_sql/config.py -> recsys_sql/
PACKAGE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """recsys_sql 이 쓰는 환경변수 전체."""

    model_config = SettingsConfigDict(
        env_file=PACKAGE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: SecretStr | None = None
    database_url_direct: SecretStr | None = None
    neon_branch: str = ""

    query_owner: str = ""
    query_timeout_seconds: float = Field(default=10.0, gt=0)
    forbid_seq_scan_on: Annotated[tuple[str, ...], NoDecode] = ("product", "recipe", "recipe_ingredient")

    @field_validator("forbid_seq_scan_on", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        """쉼표로 구분된 환경변수 문자열을 튜플로 바꿉니다."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @property
    def queries_dir(self) -> Path:
        """SQL 카탈로그 루트. 각자 폴더는 이 아래 github id 로 만듭니다."""
        return PACKAGE_DIR / "queries"

    @property
    def owner_dir(self) -> Path:
        """QUERY_OWNER 가 지정되어 있으면 그 폴더, 아니면 카탈로그 전체."""
        return self.queries_dir / self.query_owner if self.query_owner else self.queries_dir

    def require_database_url(self, *, direct: bool = False) -> str:
        """DB URL 을 꺼내되, 비어 있으면 값 노출 없이 실패시킵니다."""
        url = self.database_url_direct if direct else self.database_url
        if url is None or not url.get_secret_value().strip():
            key = "DATABASE_URL_DIRECT" if direct else "DATABASE_URL"
            raise RuntimeError(f"{key} 가 비어 있습니다. recsys_sql/.env 를 확인하세요.")
        return url.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 .env 를 읽어 재사용합니다."""
    return Settings()
