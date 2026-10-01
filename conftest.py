"""레포 전체 pytest 설정. DB 테스트가 공용 DB 에 흔적을 남기지 않았는지 세션 끝에 확인합니다.

DB 테스트는 전부 트랜잭션 안에서 돌고 롤백합니다. recsys_sql·rag_lab 의 `db_conn`, serving 의
`world`(api_test_harness), data_pipeline 의 롤백 스코프가 그렇습니다. 이 파일은 그 약속을 **확인**합니다.
CI 가 공용 Neon 브랜치에 붙어 돌기 때문에, 롤백을 빠뜨린 테스트가 하나라도 생기면 팀 데이터가 쌓입니다.

- 테스트 전용 ID 대역(9,100M recsys_sql, 9,300M serving)과 표식(`TEST-SEED`, `IT-SEED`, `PYTEST_*`)에
  행이 하나라도 남았으면 세션을 실패로 끝냅니다.
- 9,200M 대역은 데모 시드(`data-pipeline seed-scenario`)가 **일부러 남기는** 데이터라 보지 않습니다.
- 확인은 DB 테스트가 실제로 돌았고 환경변수 `DATABASE_URL` 이 있을 때만 합니다(CI). 로컬은 폴더마다
  `.env` 가 달라 확인할 대상이 하나로 정해지지 않습니다.

반대 방향도 막습니다. db 마커가 없는 단위 테스트에서는 DB·LLM 환경변수를 지워, CI 가 DB 테스트용으로
내려 준 값이 "DB 없이" 도는 테스트에 새어 들지 않게 합니다(`_isolate_unit_tests_from_ci_env`).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import LiteralString

import psycopg
import pytest

# 이 목록의 행 수가 전부 0 이어야 합니다. 새 테스트가 다른 대역·표식을 쓰면 여기에도 더합니다.
_TEST_USERS: LiteralString = "user_id BETWEEN 9100000000 AND 9199999999 OR user_id BETWEEN 9300000000 AND 9399999999"
_TEST_SOURCES: LiteralString = "source_type IN ('TEST-SEED', 'IT-SEED') OR source_type LIKE 'PYTEST%'"
RESIDUE_CHECKS: dict[str, LiteralString] = {
    "app_user": "SELECT count(*) FROM app_user WHERE " + _TEST_USERS,
    "user_fridge": "SELECT count(*) FROM user_fridge WHERE " + _TEST_USERS,
    "ingredient": "SELECT count(*) FROM ingredient "
    "WHERE source_identity_key LIKE 'TEST-SEED:%' OR source_identity_key LIKE 'IT-SEED:%'",
    "product": "SELECT count(*) FROM product WHERE " + _TEST_SOURCES,
    "recipe": "SELECT count(*) FROM recipe WHERE " + _TEST_SOURCES,
    "storage_guideline": "SELECT count(*) FROM storage_guideline WHERE storage_id BETWEEN 9100000000 AND 9399999999",
}

DB_TESTS_RAN = pytest.StashKey[bool]()

# 단위 테스트(db 마커 없음)에서 지우는 환경변수. CI 는 DB 테스트를 위해 이 값들을 프로세스 환경변수로
# 내려 주는데, `Settings(_env_file=None)` 은 `.env` 파일만 막고 환경변수는 그대로 읽습니다. 지우지 않으면
# "DB 없이" 를 전제로 한 단위 테스트가 CI 에서만 실제 DB 에 붙습니다(로컬은 값이 .env 에만 있어 드러나지 않음).
_ISOLATED_ENV = ("DATABASE_URL", "DATABASE_URL_DIRECT", "OPENROUTER_API_KEY", "REASON_MODEL")


def _clear_settings_caches() -> None:
    """패키지마다 `get_settings()` 가 lru_cache 입니다. 환경을 바꿨으면 다시 읽게 비웁니다."""
    from data_pipeline.config import get_settings as data_pipeline_settings
    from rag_lab.config import get_settings as rag_lab_settings
    from recsys_sql.config import get_settings as recsys_sql_settings
    from serving.config import get_settings as serving_settings

    for get_settings in (data_pipeline_settings, rag_lab_settings, recsys_sql_settings, serving_settings):
        get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_unit_tests_from_ci_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """db 마커가 없는 테스트는 DB·LLM 환경변수 없이 돕니다. 로컬과 CI 에서 같은 조건이 됩니다.

    테스트가 끝나면 환경변수는 monkeypatch 가 되돌리고, 설정 캐시를 다시 비워 뒤따르는 DB 테스트가
    환경변수 없는 설정을 물려받지 않게 합니다.
    """
    if request.node.get_closest_marker("db") is not None:
        yield
        return
    for key in _ISOLATED_ENV:
        monkeypatch.delenv(key, raising=False)
    _clear_settings_caches()
    yield
    _clear_settings_caches()


def residue_counts(url: str) -> dict[str, int]:
    """표별로 테스트 흔적 행 수를 셉니다. 읽기 전용 트랜잭션입니다."""
    # 폴더별 .env 는 SQLAlchemy 드라이버 표기(`postgresql+asyncpg://`)를 쓰기도 합니다. libpq 는 모릅니다.
    scheme, _, rest = url.partition("://")
    dsn = f"postgresql://{rest}" if scheme.split("+", 1)[0] in {"postgresql", "postgres"} else url
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        for table, sql in RESIDUE_CHECKS.items():
            cursor.execute(sql)
            row = cursor.fetchone()
            counts[table] = int(row[0]) if row else 0
    return counts


def pytest_runtest_call(item: pytest.Item) -> None:
    """skip 되지 않고 실제로 돈 DB 테스트가 있었는지 기억합니다."""
    if item.get_closest_marker("db") is not None:
        item.config.stash[DB_TESTS_RAN] = True


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """DB 테스트가 돌았다면 흔적을 세고, 남았으면 세션을 실패로 바꿉니다."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not session.config.stash.get(DB_TESTS_RAN, False) or not url:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    try:
        leftovers = {table: count for table, count in residue_counts(url).items() if count}
    except psycopg.Error as exc:
        # 확인하지 못한 것을 통과로 두지 않습니다. 예외 메시지에 호스트가 있을 수 있어 타입만 남깁니다.
        leftovers = {f"확인 실패({type(exc).__name__})": -1}
    if reporter is not None:
        if leftovers:
            reporter.write_line(f"공용 DB 에 테스트 흔적이 남았습니다(롤백 누락): {leftovers}", red=True)
        else:
            reporter.write_line("공용 DB 테스트 흔적 검사: 0행 (전부 롤백됨)", green=True)
    if leftovers:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
