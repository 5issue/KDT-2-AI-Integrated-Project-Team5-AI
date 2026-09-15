"""Neon DSN 변환/마스킹 테스트. DB 없이 돕니다."""

from __future__ import annotations

import pytest

from serving.config import get_settings
from serving.db import mask_dsn, normalize_neon_dsn

HOST = "ep-cool-frog-123.ap-southeast-1.aws.neon.tech"
POOLED_HOST = HOST.replace("-123.", "-123-pooler.")
POOLED = f"postgresql://alice:secret@{POOLED_HOST}/neondb?sslmode=require&channel_binding=require"
DIRECT = f"postgresql://alice:secret@{HOST}/neondb?sslmode=require"


def test_strips_libpq_only_params() -> None:
    """asyncpg 가 모르는 파라미터는 DSN 에서 빠지고 ssl 인자로 옮겨집니다."""
    dsn, kwargs = normalize_neon_dsn(POOLED)

    assert dsn.startswith("postgresql://")
    assert "sslmode" not in dsn
    assert "channel_binding" not in dsn
    assert kwargs["ssl"] == "require"


def test_disables_statement_cache_for_pooler() -> None:
    """PgBouncer transaction 모드에서는 statement 캐시를 꺼야 합니다."""
    _, kwargs = normalize_neon_dsn(POOLED)

    assert kwargs["statement_cache_size"] == 0
    assert kwargs["max_cacheable_statement_size"] == 0


def test_keeps_statement_cache_for_direct() -> None:
    """direct 엔드포인트에서는 캐시를 그대로 씁니다."""
    _, kwargs = normalize_neon_dsn(DIRECT)

    assert "statement_cache_size" not in kwargs


def test_accepts_sqlalchemy_style_scheme() -> None:
    """다른 폴더에서 쓰는 postgresql+asyncpg DSN 을 붙여 넣어도 동작합니다."""
    dsn, _ = normalize_neon_dsn(POOLED.replace("postgresql://", "postgresql+asyncpg://"))
    assert dsn.startswith("postgresql://")


def test_rejects_unknown_scheme() -> None:
    """다른 드라이버 DSN 은 조용히 넘기지 않습니다."""
    with pytest.raises(ValueError, match="지원하지 않는 DSN"):
        normalize_neon_dsn("mysql://user:pw@host/db")


def test_mask_dsn_hides_credentials_and_host() -> None:
    """로그에 남는 문자열에 자격증명이나 호스트가 들어가면 안 됩니다."""
    masked = mask_dsn(POOLED)

    assert "alice" not in masked
    assert "secret" not in masked
    assert "neon.tech" not in masked
    assert "neondb" in masked
    assert "pooled" in masked
    assert "direct" in mask_dsn(DIRECT)


def test_cors_origins_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """.env.example 이 쉼표 형식을 안내하므로 그 형식이 실제로 통해야 합니다.

    pydantic-settings 는 복합 타입 필드를 검증기보다 먼저 JSON 으로 파싱합니다.
    NoDecode 를 붙이지 않으면 field_validator 가 실행될 기회조차 없이 터집니다.
    """
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:3000, https://example.test")
    get_settings.cache_clear()
    try:
        assert get_settings().cors_allow_origins == ("http://localhost:3000", "https://example.test")
    finally:
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
        get_settings.cache_clear()
