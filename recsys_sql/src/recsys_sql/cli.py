"""recsys_sql CLI.

    uv run recsys-sql check-db
    uv run recsys-sql list
    uv run recsys-sql run --query fridge_recipe_match \
        --param user_id=1 --param min_coverage=0.5 --param max_results=5
    uv run recsys-sql explain --query fridge_recipe_match \
        --param user_id=1 --param min_coverage=0.5 --param max_results=5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from recsys_sql.catalog import CatalogError, find_query, load_catalog
from recsys_sql.config import get_settings
from recsys_sql.db import check_connection, engine_scope
from recsys_sql.runner import explain_query, run_query


def parse_param(raw: str, declared_type: str) -> Any:
    """`--param name=value` 의 값을 선언된 타입으로 바꿉니다."""
    if declared_type == "int":
        return int(raw)
    if declared_type == "float":
        return float(raw)
    if declared_type == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "y"}
    if declared_type in {"list[int]", "list[str]"}:
        items = [item.strip() for item in raw.split(",") if item.strip()]
        return [int(item) for item in items] if declared_type == "list[int]" else items
    return raw


def collect_params(query_name: str, pairs: list[str]) -> dict[str, Any]:
    """--param 들을 카탈로그 선언 타입에 맞춰 dict 로 모읍니다."""
    query = find_query(query_name)
    params: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CatalogError(f"--param 은 name=value 형식이어야 합니다: {pair!r}")
        name, _, value = pair.partition("=")
        name = name.strip()
        if name not in query.params:
            raise CatalogError(f"{query_name} 에 없는 파라미터입니다: {name}")
        params[name] = parse_param(value, query.params[name])
    return params


def command_check_db(args: argparse.Namespace) -> int:
    """Neon 연결과 스키마 상태를 점검합니다."""
    report = asyncio.run(check_connection(direct=args.direct))
    print(report.render())
    return 0 if report.ok else 1


def command_list(_: argparse.Namespace) -> int:
    """카탈로그의 쿼리 목록을 보여줍니다."""
    settings = get_settings()
    queries = load_catalog(settings.queries_dir, settings)
    if not queries:
        print("등록된 쿼리가 없습니다.")
        return 0
    for query in queries:
        params = ", ".join(f"{name}:{type_name}" for name, type_name in query.params.items())
        print(f"{query.name:32} [{query.owner}] {query.description}")
        print(f"{'':32} params: {params or '없음'}")
    return 0


async def run_and_print(query_name: str, pairs: list[str], *, explain: bool) -> int:
    """쿼리를 실행하거나 실행계획을 출력합니다."""
    settings = get_settings()
    query = find_query(query_name, settings)
    params = collect_params(query_name, pairs)

    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            if explain:
                report = await explain_query(conn, query, params)
                forbidden = report.forbidden_seq_scans(settings.forbid_seq_scan_on)
                print(f"총 비용: {report.total_cost:.1f}")
                print(f"Seq Scan: {', '.join(report.seq_scans) or '없음'}")
                if forbidden:
                    print(f"금지된 Seq Scan: {', '.join(forbidden)}", file=sys.stderr)
                    return 1
                return 0

            result = await run_query(conn, query, params, settings=settings)
            print(f"{result.query_name}: {len(result.rows)}행 / {result.elapsed_ms:.1f} ms")
            for row in result.as_dicts()[:20]:
                print("  ", row)
    return 0


def command_run(args: argparse.Namespace) -> int:
    """쿼리를 실행합니다."""
    return asyncio.run(run_and_print(args.query, args.param, explain=False))


def command_explain(args: argparse.Namespace) -> int:
    """실행계획을 확인합니다."""
    return asyncio.run(run_and_print(args.query, args.param, explain=True))


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만듭니다."""
    parser = argparse.ArgumentParser(prog="recsys-sql", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-db", help="Neon 연결/스키마 점검")
    check.add_argument("--direct", action="store_true")
    check.set_defaults(func=command_check_db)

    listing = sub.add_parser("list", help="카탈로그 쿼리 목록")
    listing.set_defaults(func=command_list)

    for name, help_text, func in (
        ("run", "쿼리 실행", command_run),
        ("explain", "실행계획 확인", command_explain),
    ):
        sub_parser = sub.add_parser(name, help=help_text)
        sub_parser.add_argument("--query", required=True)
        sub_parser.add_argument("--param", action="append", default=[], help="name=value 형식, 반복 가능")
        sub_parser.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, FileNotFoundError, KeyError, ValueError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
