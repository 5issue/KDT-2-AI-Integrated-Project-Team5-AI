"""serving CLI.

uv run serving run                 # 개발 서버 (자동 리로드)
uv run serving run --no-reload     # 리로드 없이
uv run serving check-db            # 풀을 만들어 왕복 한 번 확인
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from serving.config import get_settings
from serving.db import check_health, create_pool, mask_dsn


async def check_db() -> int:
    """커넥션 풀을 만들어 헬스체크를 한 번 돌립니다."""
    settings = get_settings()
    print(f"연결 대상: {mask_dsn(settings.require_database_url())}")

    pool = await create_pool(settings)
    try:
        health = await check_health(pool, settings=settings)
    finally:
        await pool.close()

    if not health.ok:
        print(f"연결 실패: {health.detail}", file=sys.stderr)
        return 1

    print(f"서버 버전 : {health.server_version}")
    print(f"database  : {health.database}")
    print(f"왕복 지연 : {health.latency_ms:.1f} ms")
    print(f"풀        : size={health.pool_size} idle={health.pool_idle}")
    for note in health.notes:
        print(f"참고      : {note}")
    return 0


def command_check_db(_: argparse.Namespace) -> int:
    """DB 연결 점검."""
    return asyncio.run(check_db())


def command_run(args: argparse.Namespace) -> int:
    """uvicorn 으로 서버를 띄웁니다."""
    settings = get_settings()
    uvicorn.run(
        "serving.app:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만듭니다."""
    parser = argparse.ArgumentParser(prog="serving", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-db", help="DB 연결 점검")
    check.set_defaults(func=command_check_db)

    run = sub.add_parser("run", help="개발 서버 실행")
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument("--reload", action=argparse.BooleanOptionalAction, default=True)
    run.set_defaults(func=command_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, OSError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
