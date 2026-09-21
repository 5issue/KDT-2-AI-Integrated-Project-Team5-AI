"""KIPIL 카탈로그 승격 스크립트의 DB 접속 전 안전장치 테스트입니다."""

from __future__ import annotations

import io

import pytest

from scripts.db._env import validate_confirmation
from scripts.db.promote_kipil_catalog import (
    CATALOG_TABLES,
    DEPENDENT_TABLES,
    MUST_BE_EMPTY,
    PRODUCTION_CONFIRMATION,
    WIPED_TABLES,
    _write_prefix,
    parse_connection,
)


def test_production_apply_requires_exact_confirmation() -> None:
    """Production 쓰기에는 확인 문자열이 정확히 일치해야 합니다."""
    with pytest.raises(ValueError, match="Production 적용"):
        validate_confirmation("production", True, None, token=PRODUCTION_CONFIRMATION)
    validate_confirmation("production", True, PRODUCTION_CONFIRMATION, token=PRODUCTION_CONFIRMATION)


def test_dry_run_does_not_require_confirmation() -> None:
    """읽기만 하는 dry-run 은 확인 문자열 없이 돕니다."""
    validate_confirmation("production", False, None, token=PRODUCTION_CONFIRMATION)
    validate_confirmation("local", False, None, token=PRODUCTION_CONFIRMATION)


def test_localhost_is_rewritten_only_for_docker_target() -> None:
    """컨테이너에서 로컬 DB 에 닿아야 할 때만 호스트를 바꿉니다."""
    url = "postgresql://user:password@127.0.0.1:55433/database"

    assert parse_connection(url).host == "127.0.0.1"
    assert parse_connection(url, docker_target=True).host == "host.docker.internal"


def test_percent_encoded_credentials_are_decoded() -> None:
    """URL 인코딩된 비밀번호를 원래 값으로 되돌려 넘깁니다."""
    parsed = parse_connection("postgresql://user:p%40ss@db.example.com/database")

    assert parsed.password == "p@ss"


def test_truncate_never_uses_cascade() -> None:
    """CASCADE 는 열거하지 않은 표까지 비웁니다. 쓰지 않습니다."""
    script = io.StringIO()

    _write_prefix(script, "backup_schema")

    assert "CASCADE" not in script.getvalue()


def test_every_truncated_table_is_backed_up_first() -> None:
    """비우는 표는 예외 없이 먼저 백업합니다."""
    script = io.StringIO()

    _write_prefix(script, "backup_schema")
    text = script.getvalue()

    backup_at = {table: text.index(f'"backup_schema"."{table}"') for table in WIPED_TABLES}
    truncate_at = text.index("TRUNCATE TABLE ")
    for table in WIPED_TABLES:
        assert f'public."{table}"' in text.split("TRUNCATE TABLE ")[1]
        assert backup_at[table] < truncate_at


def test_dependent_tables_are_wiped_explicitly() -> None:
    """카탈로그를 참조하는 표는 CASCADE 가 아니라 이름으로 비웁니다."""
    assert "storage_guideline" in WIPED_TABLES
    assert "storage_guideline" not in CATALOG_TABLES
    assert set(DEPENDENT_TABLES).isdisjoint(CATALOG_TABLES)


def test_orders_are_rechecked_after_locking() -> None:
    """잠근 뒤에 다시 세지 않으면 검사와 삭제 사이에 들어온 주문이 백업 없이 사라집니다."""
    script = io.StringIO()

    _write_prefix(script, "backup_schema")
    text = script.getvalue()

    lock_at = text.index("LOCK TABLE ")
    for table in MUST_BE_EMPTY:
        assert text.index(f"RAISE EXCEPTION '대상 DB의 {table}") > lock_at


def test_constraints_are_deferred_inside_the_restore_transaction() -> None:
    """COPY 순서가 부모·자식을 보장하지 않으므로 self FK 검사를 커밋 시점으로 미룹니다."""
    script = io.StringIO()

    _write_prefix(script, "backup_schema")
    text = script.getvalue()

    assert "SET CONSTRAINTS ALL DEFERRED;" in text
    assert text.index("SET CONSTRAINTS ALL DEFERRED;") < text.index("TRUNCATE TABLE ")
