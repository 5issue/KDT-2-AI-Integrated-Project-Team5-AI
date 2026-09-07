"""data_pipeline CLI.

레포 루트에서 실행할 때는 콘솔 스크립트를 쓰세요. 루트에 data_pipeline/ 디렉터리가 있어서
`python -m data_pipeline.cli` 는 그 디렉터리를 네임스페이스 패키지로 잡아 실패합니다.

    uv run data-pipeline check-db
    uv run data-pipeline inspect                       # data/raw 확인 (parquet/JSONL)
    uv run data-pipeline build  --job recipes_20260908
    uv run data-pipeline submit --job recipes_20260907
    uv run data-pipeline collect --job recipes_20260907 --wait
    uv run data-pipeline load   --job recipes_20260907
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from data_pipeline.batch.build_requests import build_recipe_batch_input
from data_pipeline.batch.collect import download_results, refresh_jobs, wait_until_done
from data_pipeline.batch.raw_source import preview_raw_source
from data_pipeline.batch.results import parse_recipe_results
from data_pipeline.batch.submit import load_manifest, submit_batch_files
from data_pipeline.config import get_settings
from data_pipeline.db import check_connection
from data_pipeline.load.bulk_insert import run_load


def command_check_db(args: argparse.Namespace) -> int:
    """Neon 연결과 스키마 상태를 점검합니다."""
    report = asyncio.run(check_connection(direct=args.direct))
    print(report.render())
    if not report.ok:
        print("\n연결은 되었지만 기대한 스키마가 아닙니다. Neon 브랜치를 확인하세요.", file=sys.stderr)
        return 1
    return 0


def resolve_raw_path(raw: str | None) -> Path:
    """--raw 를 생략하면 설정의 data/raw 를 씁니다."""
    return Path(raw) if raw else get_settings().raw_dir


def command_inspect(args: argparse.Namespace) -> int:
    """raw 데이터를 배치에 넣기 전에 눈으로 확인합니다. DB/API 없이 됩니다."""
    print(preview_raw_source(resolve_raw_path(args.raw), limit=args.limit))
    return 0


def command_build(args: argparse.Namespace) -> int:
    """raw(parquet/JSONL) -> Batch API 입력 JSONL."""
    paths = build_recipe_batch_input(resolve_raw_path(args.raw), job_name=args.job)
    for path in paths:
        print(f"생성: {path.name}")
    return 0


def command_submit(args: argparse.Namespace) -> int:
    """입력 JSONL 을 업로드하고 배치를 만듭니다."""
    settings = get_settings()
    inputs = sorted(settings.batch_dir.glob(f"{args.job}_part*_input.jsonl"))
    if not inputs:
        print(f"{args.job} 의 입력 파일이 없습니다. 먼저 build 를 실행하세요.", file=sys.stderr)
        return 1
    for job in submit_batch_files(inputs, job_name=args.job):
        print(f"제출: {job.input_file} -> {job.batch_id} ({job.status})")
    return 0


def command_collect(args: argparse.Namespace) -> int:
    """배치 상태를 갱신하고 완료된 결과를 내려받습니다."""
    jobs = wait_until_done(args.job, poll_seconds=args.poll) if args.wait else refresh_jobs(args.job)
    for job in jobs:
        print(f"{job.batch_id}: {job.status}")
    if any(job.status != "completed" for job in jobs):
        print("아직 완료되지 않은 배치가 있습니다.", file=sys.stderr)
        return 1
    for path in download_results(args.job):
        print(f"다운로드: {path.name}")
    return 0


def command_load(args: argparse.Namespace) -> int:
    """배치 결과를 검증하고 Neon 에 벌크 적재합니다."""
    settings = get_settings()
    load_manifest(args.job, settings)  # 잡 이름이 유효한지 먼저 확인
    outcome = parse_recipe_results(settings.batch_dir)
    print(outcome.summary())
    for failure in outcome.failures[:20]:
        print(f"  실패 {failure.custom_id}: {failure.reason}")
    if not outcome.records:
        print("적재할 레코드가 없습니다.", file=sys.stderr)
        return 1
    report = asyncio.run(run_load(outcome.records, settings=settings))
    print(report.render())
    return 0


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만듭니다."""
    parser = argparse.ArgumentParser(prog="data_pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-db", help="Neon 연결/스키마 점검")
    check.add_argument("--direct", action="store_true", help="pooler 대신 direct 엔드포인트로 접속")
    check.set_defaults(func=command_check_db)

    inspect = sub.add_parser("inspect", help="raw 데이터 확인 (컬럼, 행 수, 중복 id, 본문 샘플)")
    inspect.add_argument("--raw", help="raw 경로. 생략하면 data_pipeline/data/raw")
    inspect.add_argument("--limit", type=int, default=3, help="본문 샘플 개수")
    inspect.set_defaults(func=command_inspect)

    build = sub.add_parser("build", help="raw(parquet/JSONL) -> 배치 입력 JSONL")
    build.add_argument("--raw", help="parquet/JSONL 파일 또는 디렉터리. 생략하면 data_pipeline/data/raw")
    build.add_argument("--job", required=True, help="잡 이름 (파일 접두사로 쓰입니다)")
    build.set_defaults(func=command_build)

    submit = sub.add_parser("submit", help="배치 제출")
    submit.add_argument("--job", required=True)
    submit.set_defaults(func=command_submit)

    collect = sub.add_parser("collect", help="배치 상태 확인 및 결과 다운로드")
    collect.add_argument("--job", required=True)
    collect.add_argument("--wait", action="store_true", help="완료될 때까지 폴링")
    collect.add_argument("--poll", type=int, default=60, help="폴링 간격(초)")
    collect.set_defaults(func=command_collect)

    load = sub.add_parser("load", help="결과 검증 + Neon 벌크 적재")
    load.add_argument("--job", required=True)
    load.set_defaults(func=command_load)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
