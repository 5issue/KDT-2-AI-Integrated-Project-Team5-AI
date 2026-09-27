"""KIPIL 핵심 카탈로그를 스키마 준비가 끝난 대상 DB로 복제합니다.

기본 동작은 양쪽 DB의 건수만 비교하는 dry-run입니다. 실제 복제는 pg_dump와 psql을
임시 컨테이너에서 실행하며, 접속 비밀번호는 명령 인자가 아니라 프로세스 환경으로만
전달합니다. Production 적용에는 별도 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg

from scripts.db._env import Target, env_url, load_env, validate_confirmation

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "pgvector/pgvector:pg18"
PRODUCTION_CONFIRMATION = "PROMOTE_KIPIL_CATALOG_V1"

# KIPIL 에서 복제해 오는 표입니다.
CATALOG_TABLES = (
    "category",
    "ingredient",
    "product",
    "product_ingredient",
    "recipe",
    "recipe_ingredient",
    "recipe_step",
    "recipe_product",
    "product_popularity",
    "app_user",
    "user_fridge",
    "user_product_affinity",
)

# 카탈로그를 참조하므로 함께 비워야 하지만 복제하지는 않는 표입니다.
# 예전에는 `TRUNCATE ... CASCADE` 가 이 표들을 조용히 비웠고 백업에도 없었습니다.
# 명시적으로 올려 두어야 백업이 되고, 새 표가 생기면 바로 드러납니다.
DEPENDENT_TABLES = (
    "storage_guideline",
    "order_item",
    "order_header",
)

# 백업과 TRUNCATE 의 대상입니다. 여기 없는 표는 이 스크립트가 건드리지 않습니다.
WIPED_TABLES = CATALOG_TABLES + DEPENDENT_TABLES

# 비우기 전에 반드시 비어 있어야 하는 표입니다. 주문은 복구할 원천이 없습니다.
MUST_BE_EMPTY = ("order_header", "order_item")


@dataclass(frozen=True, slots=True)
class ConnectionParts:
    host: str
    port: int
    user: str
    password: str
    database: str
    sslmode: str


def parse_connection(url: str, *, docker_target: bool = False) -> ConnectionParts:
    """libpq URL을 Docker의 pg_dump와 psql 인자로 분해합니다.

    URL 의 `sslmode` 를 그대로 가져옵니다. 여기서 `require` 로 고정해 버리면 URL 이
    `verify-full` 을 요구해도 인증서를 확인하지 않는 연결로 조용히 내려갑니다.
    `sslrootcert` 는 컨테이너 안에 그 파일이 없으므로 받지 않고 멈춥니다.
    """
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise ValueError("DB 접속 문자열에 host, user, password가 모두 필요합니다.")
    query = parse_qs(parsed.query)
    if query.get("sslrootcert"):
        raise ValueError("sslrootcert 는 컨테이너 안에서 읽을 수 없습니다. 인증서를 넣는 경로를 먼저 정하세요.")
    sslmode = query.get("sslmode", ["require"])[0]
    if sslmode in {"disable", "allow", "prefer"}:
        raise ValueError(f"암호화되지 않을 수 있는 sslmode={sslmode} 로는 카탈로그를 옮기지 않습니다.")
    host = parsed.hostname
    if docker_target and host in {"127.0.0.1", "localhost"}:
        host = "host.docker.internal"
    return ConnectionParts(
        host=host,
        port=parsed.port or 5432,
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        database=parsed.path.lstrip("/") or "postgres",
        sslmode=sslmode,
    )


def database_identity(url: str) -> tuple[str, str, str]:
    """서버와 데이터베이스의 실제 식별자를 읽습니다.

    URL 문자열 비교로는 같은 DB 를 가리키는 다른 주소를 구분하지 못합니다. Neon 은 같은
    브랜치에 pooler 주소와 직접 주소를 함께 주고, 포트 표기나 쿼리 매개변수도 다를 수
    있습니다. 같은 DB 를 source 와 target 으로 쓰면 복제하지 않는 표까지 비웁니다.

    Neon 브랜치는 한 프로젝트에서 갈라져 나와 `system_identifier` 와 database 이름이
    서로 같습니다. 브랜치를 가르는 값은 `neon.timeline_id` 뿐이라 함께 읽습니다.
    Neon 이 아닌 서버에서는 이 설정이 없어 빈 문자열이 되고, `system_identifier` 가
    클러스터를 가릅니다.
    """
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT system_identifier::text, "
            "COALESCE(current_setting('neon.timeline_id', true), ''), "
            "current_database() FROM pg_control_system()"
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("DB 식별자를 읽지 못했습니다.")
        return str(row[0]), str(row[1]), str(row[2])


def fetch_counts(url: str) -> dict[str, int]:
    """복제 대상 표의 행 수와 안전성 관련 주문 건수를 읽습니다."""
    counts: dict[str, int] = {}
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        alembic_row = cursor.fetchone()
        if alembic_row is None or not alembic_row[0]:
            raise ValueError("대상 DB에 alembic_version이 없습니다. schema migration을 먼저 적용하세요.")
        for table in WIPED_TABLES:
            cursor.execute(f'SELECT count(*) FROM public."{table}"')
            count_row = cursor.fetchone()
            if count_row is None:
                raise RuntimeError(f"{table} 행 수 조회 결과가 없습니다.")
            counts[table] = int(count_row[0])
    return counts


def verify_reference_closure(url: str) -> None:
    """카탈로그를 참조하는 표가 전부 `WIPED_TABLES` 안에 있는지 확인합니다.

    밖에 있는 표가 하나라도 생기면 TRUNCATE 가 FK 오류로 죽습니다. 그 오류를 읽는 대신
    여기서 이름을 알려 줍니다. 예전처럼 CASCADE 로 덮어 버리면 백업 없이 사라집니다.
    """
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT src.relname
            FROM pg_constraint co
            JOIN pg_class src ON src.oid = co.conrelid
            JOIN pg_class tgt ON tgt.oid = co.confrelid
            JOIN pg_namespace ns ON ns.oid = src.relnamespace
            WHERE co.contype = 'f' AND ns.nspname = 'public' AND tgt.relname = ANY(%s)
            """,
            (list(CATALOG_TABLES),),
        )
        referencing = {row[0] for row in cursor.fetchall()}
    unexpected = sorted(referencing - set(WIPED_TABLES))
    if unexpected:
        raise ValueError(f"카탈로그를 참조하는데 백업 대상이 아닌 표가 있습니다: {unexpected}")


def _docker_pg_command(tool: str, connection: ConnectionParts, extra: list[str]) -> tuple[list[str], dict[str, str]]:
    """임시 컨테이너에서 돌릴 pg 도구 명령과 환경을 만듭니다. 비밀번호는 인자가 아닌 환경으로만 넘깁니다."""
    environment = dict(os.environ)
    environment["PGPASSWORD"] = connection.password
    environment["PGSSLMODE"] = connection.sslmode
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "-e",
        "PGPASSWORD",
        "-e",
        "PGSSLMODE",
        POSTGRES_IMAGE,
        tool,
        "--host",
        connection.host,
        "--port",
        str(connection.port),
        "--username",
        connection.user,
        "--dbname",
        connection.database,
        *extra,
    ]
    return command, environment


def _write_prefix(handle: TextIO, backup_schema: str) -> None:
    """백업과 비우기를 한 트랜잭션 안에 순서대로 적습니다.

    잠그고 → 다시 세고 → 백업하고 → 비웁니다. 잠그기 전의 검사는 검사와 삭제 사이에 들어온
    행을 못 봅니다. 그 행은 백업되지 않은 채 사라지므로 잠근 뒤에 다시 셉니다.

    `CASCADE` 는 쓰지 않습니다. 열거하지 않은 표가 조용히 비는 것을 막기 위해서입니다.
    참조가 남아 있으면 TRUNCATE 가 실패하고, 그 실패가 안전장치입니다.
    """
    locked = ", ".join(f'public."{table}"' for table in WIPED_TABLES)
    handle.write(f"LOCK TABLE {locked} IN ACCESS EXCLUSIVE MODE;\n")
    # pg_dump 의 COPY 는 물리 순서로 나가서 자식 행이 부모보다 먼저 올 수 있습니다.
    # ingredient 의 self FK 처럼 DEFERRABLE 한 제약은 커밋 시점에 한 번에 검사합니다.
    handle.write("SET CONSTRAINTS ALL DEFERRED;\n")
    for table in MUST_BE_EMPTY:
        handle.write(
            "DO $$ DECLARE n bigint; BEGIN "
            f'SELECT count(*) INTO n FROM public."{table}"; '
            f"IF n > 0 THEN RAISE EXCEPTION '대상 DB의 {table} 에 % 행이 있어 중단합니다', n; END IF; "
            "END $$;\n"
        )
    handle.write(f'CREATE SCHEMA "{backup_schema}";\n')
    for table in WIPED_TABLES:
        handle.write(f'CREATE TABLE "{backup_schema}"."{table}" AS TABLE public."{table}";\n')
    joined = ", ".join(f'public."{table}"' for table in reversed(WIPED_TABLES))
    handle.write(f"TRUNCATE TABLE {joined};\n")


def _append_dump(handle: TextIO, source: ConnectionParts) -> None:
    """복제 대상 표의 데이터 덤프를 백업·비우기 SQL 뒤에 이어 붙입니다."""
    # Python의 text buffer를 먼저 비우지 않으면 subprocess가 같은 파일 descriptor의
    # 앞부분부터 써서 backup/truncate SQL보다 COPY가 먼저 배치됩니다.
    handle.flush()
    tables = [argument for table in CATALOG_TABLES for argument in ("--table", table)]
    command, environment = _docker_pg_command(
        "pg_dump",
        source,
        ["--data-only", "--no-owner", "--no-privileges", "--strict-names", "--schema", "public", *tables],
    )
    # check=True 라 실패하면 CalledProcessError 로 바로 올라옵니다.
    subprocess.run(command, env=environment, check=True, text=True, stdout=handle)


def _restore(script_path: Path, target: ConnectionParts) -> None:
    """만든 SQL 을 대상 DB에서 한 트랜잭션으로 실행합니다. 중간에 실패하면 전부 롤백됩니다."""
    command, environment = _docker_pg_command(
        "psql",
        target,
        ["--single-transaction", "--set", "ON_ERROR_STOP=1", "--file", "/dev/stdin"],
    )
    with script_path.open(encoding="utf-8") as script:
        subprocess.run(command, env=environment, check=True, text=True, stdin=script)


def promote(source_url: str, target_url: str, *, target: Target, apply: bool) -> tuple[dict[str, int], str | None]:
    """건수 비교 또는 트랜잭션 단위 카탈로그 복제를 수행합니다."""
    if source_url == target_url:
        raise ValueError("source와 target DB가 같습니다.")
    source_identity = database_identity(source_url)
    if source_identity == database_identity(target_url):
        raise ValueError(
            f"source와 target이 같은 DB입니다"
            f"(서버 {source_identity[0]}, timeline {source_identity[1]}, database {source_identity[2]})."
        )
    source_counts = fetch_counts(source_url)
    target_counts = fetch_counts(target_url)
    verify_reference_closure(target_url)
    for table in MUST_BE_EMPTY:
        if target_counts[table]:
            raise ValueError(f"대상 DB의 {table} 에 {target_counts[table]} 행이 있어 카탈로그 교체를 중단합니다.")
    if not apply:
        return {f"source_{key}": value for key, value in source_counts.items()} | {
            f"target_{key}": value for key, value in target_counts.items()
        }, None

    backup_schema = f"kipil_catalog_backup_{datetime.now(UTC):%Y%m%d%H%M%S}"
    if not re.fullmatch(r"[a-z0-9_]+", backup_schema):
        raise ValueError("백업 스키마 이름이 안전하지 않습니다.")
    source = parse_connection(source_url)
    target_connection = parse_connection(target_url, docker_target=target == "local")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".sql", delete=False) as handle:
        script_path = Path(handle.name)
        text_handle = cast(TextIO, handle)
        _write_prefix(text_handle, backup_schema)
        _append_dump(text_handle, source)
    try:
        _restore(script_path, target_connection)
    finally:
        script_path.unlink(missing_ok=True)

    after = fetch_counts(target_url)
    mismatches = {
        table: (source_counts[table], after[table]) for table in CATALOG_TABLES if source_counts[table] != after[table]
    }
    if mismatches:
        raise RuntimeError(f"복제 후 행 수가 다릅니다: {mismatches}")
    return after, backup_schema


def parse_args() -> argparse.Namespace:
    """명령행 인자를 읽습니다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--source-url-env", default="DATABASE_URL_KIPIL")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    """건수를 보고하고, --apply 일 때만 카탈로그를 교체합니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production, token=PRODUCTION_CONFIRMATION)
    values = load_env(args.env_file)
    source_url = env_url(values, args.source_url_env)
    target_url = env_url(values, args.target_url_env)
    counts, backup_schema = promote(source_url, target_url, target=target, apply=args.apply)
    print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
    print(f"backup_schema={backup_schema}")
    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    if args.apply:
        # 복제 대상이 아니라 비우기만 한 표입니다. 다시 채우지 않으면 화면에서 보관법이 사라집니다.
        print("reload_required=storage_guideline (scripts/db/load_storage_guideline.py 를 다시 실행하세요)")


if __name__ == "__main__":
    main()
