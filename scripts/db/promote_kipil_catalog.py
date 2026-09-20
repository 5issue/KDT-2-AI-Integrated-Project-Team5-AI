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
from typing import Literal, TextIO, cast
from urllib.parse import unquote, urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "pgvector/pgvector:pg18"
PRODUCTION_CONFIRMATION = "PROMOTE_KIPIL_CATALOG_V1"
Target = Literal["local", "production"]

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


@dataclass(frozen=True, slots=True)
class ConnectionParts:
    host: str
    port: int
    user: str
    password: str
    database: str


def load_env(path: Path) -> dict[str, str]:
    """비밀 값을 출력하지 않고 단순 env 파일을 읽습니다."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_url(values: dict[str, str], key: str) -> str:
    value = os.environ.get(key) or values.get(key)
    if not value:
        raise ValueError(f"{key}가 설정되지 않았습니다. 접속 문자열 값은 출력하지 않습니다.")
    return value


def parse_connection(url: str, *, docker_target: bool = False) -> ConnectionParts:
    """libpq URL을 Docker의 pg_dump와 psql 인자로 분해합니다."""
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise ValueError("DB 접속 문자열에 host, user, password가 모두 필요합니다.")
    host = parsed.hostname
    if docker_target and host in {"127.0.0.1", "localhost"}:
        host = "host.docker.internal"
    return ConnectionParts(
        host=host,
        port=parsed.port or 5432,
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        database=parsed.path.lstrip("/") or "postgres",
    )


def validate_confirmation(target: Target, apply: bool, confirmation: str | None) -> None:
    if target == "production" and apply and confirmation != PRODUCTION_CONFIRMATION:
        raise ValueError(f"Production 적용에는 --confirm-production {PRODUCTION_CONFIRMATION} 이 필요합니다.")


def fetch_counts(url: str) -> dict[str, int]:
    """복제 대상 표의 행 수와 안전성 관련 주문 건수를 읽습니다."""
    counts: dict[str, int] = {}
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        alembic_row = cursor.fetchone()
        if alembic_row is None or not alembic_row[0]:
            raise ValueError("대상 DB에 alembic_version이 없습니다. schema migration을 먼저 적용하세요.")
        for table in CATALOG_TABLES:
            cursor.execute(f'SELECT count(*) FROM public."{table}"')
            count_row = cursor.fetchone()
            if count_row is None:
                raise RuntimeError(f"{table} 행 수 조회 결과가 없습니다.")
            counts[table] = int(count_row[0])
        cursor.execute("SELECT (SELECT count(*) FROM public.order_header), (SELECT count(*) FROM public.order_item)")
        order_row = cursor.fetchone()
        if order_row is None:
            raise RuntimeError("주문 행 수 조회 결과가 없습니다.")
        orders, items = order_row
        counts["order_header"] = int(orders)
        counts["order_item"] = int(items)
    return counts


def _docker_pg_command(tool: str, connection: ConnectionParts, extra: list[str]) -> tuple[list[str], dict[str, str]]:
    environment = dict(os.environ)
    environment["PGPASSWORD"] = connection.password
    environment["PGSSLMODE"] = "require"
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
    handle.write(f'CREATE SCHEMA "{backup_schema}";\n')
    for table in CATALOG_TABLES:
        handle.write(f'CREATE TABLE "{backup_schema}"."{table}" AS TABLE public."{table}";\n')
    joined = ", ".join(f'public."{table}"' for table in reversed(CATALOG_TABLES))
    handle.write(f"TRUNCATE TABLE {joined} CASCADE;\n")


def _append_dump(handle: TextIO, source: ConnectionParts) -> None:
    # Python의 text buffer를 먼저 비우지 않으면 subprocess가 같은 파일 descriptor의
    # 앞부분부터 써서 backup/truncate SQL보다 COPY가 먼저 배치됩니다.
    handle.flush()
    tables = [argument for table in CATALOG_TABLES for argument in ("--table", table)]
    command, environment = _docker_pg_command(
        "pg_dump",
        source,
        ["--data-only", "--no-owner", "--no-privileges", "--strict-names", "--schema", "public", *tables],
    )
    completed = subprocess.run(command, env=environment, check=True, text=True, stdout=handle)
    if completed.returncode != 0:
        raise RuntimeError("KIPIL pg_dump가 실패했습니다.")


def _restore(script_path: Path, target: ConnectionParts) -> None:
    command, environment = _docker_pg_command(
        "psql",
        target,
        ["--single-transaction", "--set", "ON_ERROR_STOP=1", "--file", "/dev/stdin"],
    )
    with script_path.open(encoding="utf-8") as script:
        subprocess.run(command, env=environment, check=True, text=True, stdin=script)


def promote(source_url: str, target_url: str, *, target: Target, apply: bool) -> tuple[dict[str, int], str | None]:
    """건수 비교 또는 트랜잭션 단위 카탈로그 복제를 수행합니다."""
    source_counts = fetch_counts(source_url)
    target_counts = fetch_counts(target_url)
    if source_url == target_url:
        raise ValueError("source와 target DB가 같습니다.")
    if target_counts["order_header"] or target_counts["order_item"]:
        raise ValueError("대상 DB에 주문 데이터가 있어 카탈로그 교체를 중단합니다.")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--source-url-env", default="DATABASE_URL_KIPIL")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production)
    values = load_env(args.env_file)
    source_url = env_url(values, args.source_url_env)
    target_url = env_url(values, args.target_url_env)
    counts, backup_schema = promote(source_url, target_url, target=target, apply=args.apply)
    print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
    print(f"backup_schema={backup_schema}")
    for key in sorted(counts):
        print(f"{key}={counts[key]}")


if __name__ == "__main__":
    main()
