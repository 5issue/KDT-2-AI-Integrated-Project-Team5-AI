"""KIPIL 카탈로그 승격 스크립트의 DB 접속 전 안전장치 테스트입니다."""

from __future__ import annotations

import pytest

from scripts.db.promote_kipil_catalog import (
    PRODUCTION_CONFIRMATION,
    parse_connection,
    validate_confirmation,
)


def test_production_apply_requires_exact_confirmation() -> None:
    with pytest.raises(ValueError, match="Production 적용"):
        validate_confirmation("production", True, None)
    validate_confirmation("production", True, PRODUCTION_CONFIRMATION)


def test_dry_run_does_not_require_confirmation() -> None:
    validate_confirmation("production", False, None)
    validate_confirmation("local", False, None)


def test_localhost_is_rewritten_only_for_docker_target() -> None:
    url = "postgresql://user:password@127.0.0.1:55433/database"

    assert parse_connection(url).host == "127.0.0.1"
    assert parse_connection(url, docker_target=True).host == "host.docker.internal"


def test_percent_encoded_credentials_are_decoded() -> None:
    parsed = parse_connection("postgresql://user:p%40ss@db.example.com/database")

    assert parsed.password == "p@ss"
