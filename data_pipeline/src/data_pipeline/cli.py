"""data_pipeline CLI. 3단계 파이프라인을 단계별로 돌립니다.

레포 루트에 data_pipeline/ 디렉터리가 있어서 `python -m data_pipeline.cli` 는 그 디렉터리를
네임스페이스 패키지로 잡아 실패합니다. 콘솔 스크립트를 쓰세요.

    uv run data-pipeline check-db
    uv run data-pipeline inspect                      # raw 데이터셋 확인
    uv run data-pipeline status                       # 단계별 산출물 현황

    uv run data-pipeline profile --job p1             # 1단계 요청 생성
    uv run data-pipeline submit  --stage profile --job p1
    uv run data-pipeline collect --stage profile --job p1 --wait

    uv run data-pipeline extract --job x1             # 2단계 (프로파일 필요)
    uv run data-pipeline submit  --stage extract --job x1
    uv run data-pipeline collect --stage extract --job x1 --wait

    uv run data-pipeline resolve --job r1             # 3단계 (정확 일치 + LLM 요청 생성)
    uv run data-pipeline submit  --stage resolve --job r1
    uv run data-pipeline collect --stage resolve --job r1 --wait

    uv run data-pipeline load                         # staging -> 타깃 테이블
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from data_pipeline.batch.client import DEAD_STATUSES, TERMINAL_STATUSES, BatchRunner
from data_pipeline.batch.raw_source import preview_raw_source
from data_pipeline.config import Settings, get_settings
from data_pipeline.db import check_connection
from data_pipeline.load.bulk_insert import collect_rows, run_load
from data_pipeline.stages import (
    STAGE_EXTRACT,
    STAGE_PROFILE,
    STAGE_RESOLVE,
    canonical,
    constraints,
    extract,
    profile,
    resolve,
)

STAGE_NAMES = {"profile": STAGE_PROFILE, "extract": STAGE_EXTRACT, "resolve": STAGE_RESOLVE}


def raw_path(args: argparse.Namespace, settings: Settings) -> Path:
    """--raw 를 생략하면 설정의 data/raw 를 씁니다."""
    return Path(args.raw) if getattr(args, "raw", None) else settings.raw_dir


def command_check_db(args: argparse.Namespace) -> int:
    """Neon 연결과 스키마 상태를 점검합니다."""
    report = asyncio.run(check_connection(direct=args.direct))
    print(report.render())
    return 0 if report.ok else 1


def command_inspect(args: argparse.Namespace) -> int:
    """raw 데이터셋을 확인합니다. DB/API 없이 됩니다."""
    print(preview_raw_source(raw_path(args, get_settings()), limit=args.limit))
    return 0


def command_status(_: argparse.Namespace) -> int:
    """단계별 산출물이 어디까지 만들어졌는지 봅니다."""
    settings = get_settings()
    print(f"raw          : {settings.raw_dir}")
    for label, stage in STAGE_NAMES.items():
        stage_root = settings.stage_dir(stage)
        requests = len(list((stage_root / "requests").glob("*_input.jsonl"))) if stage_root.exists() else 0
        results = len(list((stage_root / "results").glob("*_output.jsonl"))) if stage_root.exists() else 0
        print(f"{label:<13}: 요청 파일 {requests}개 / 결과 파일 {results}개")

    profiles = profile.profiles_path(settings)
    print(f"프로파일     : {'있음' if profiles.exists() else '없음'} ({profiles.name})")
    records_dir = settings.artifacts_dir / "records"
    if records_dir.exists():
        for path in sorted(records_dir.glob("*.jsonl")):
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            print(f"  추출 결과   : {path.stem:32} {count}건")
    matches = resolve.matches_path(settings)
    print(f"매칭 결과    : {'있음' if matches.exists() else '없음'} ({matches.name})")
    return 0


def command_profile(args: argparse.Namespace) -> int:
    """1단계: 데이터셋 프로파일링 요청을 만듭니다."""
    settings = get_settings()
    paths = profile.build(raw_path(args, settings), job_name=args.job, settings=settings)
    for path in paths:
        print(f"생성: {path.name}")
    return 0


def command_extract(args: argparse.Namespace) -> int:
    """2단계: 프로파일에 따라 추출 요청을 만듭니다."""
    settings = get_settings()
    profiles = profile.load_profiles(settings)
    paths, counts = extract.build(
        job_name=args.job, raw_path=raw_path(args, settings), profiles=profiles, settings=settings
    )
    for dataset, count in sorted(counts.items()):
        print(f"  {dataset:32} {count}건")
    for path in paths:
        print(f"생성: {path.name}")
    return 0


async def resolve_async(job_name: str, settings: Settings) -> int:
    """3단계: 정확 일치 -> 클러스터 전파 -> 남은 대표만 LLM 요청으로."""
    records_dir = settings.artifacts_dir / "records"
    names = resolve.collect_names(records_dir)
    if not names:
        print("2단계 산출물에 재료명이 없습니다.", file=sys.stderr)
        return 1

    exact, remaining = await resolve.exact_match(names, settings)

    # 같은 영문 재료의 한국어 변형끼리 결과를 나눠 씁니다. `eggs` 가 `달걀` 로 붙으면
    # `계란` 도 같이 붙습니다. 번역이 어느 쪽으로 나왔든 매칭이 흔들리지 않게 하려는 것입니다.
    clusters = canonical.build_clusters(records_dir)
    resolved = {item.normalized_name: item.ingredient_id for item in exact}
    gained = canonical.propagate(clusters, resolved)
    by_name = {item.normalized_name: item for item in remaining}
    exact = exact + [
        resolve.MatchResult(
            normalized_name=key,
            ingredient_id=ingredient_id,
            matched_name=next(m.matched_name for m in exact if m.ingredient_id == ingredient_id),
            method="cluster",
            confidence=1.0,
        )
        for key, ingredient_id in sorted(gained.items())
    ]
    remaining = [item for item in remaining if item.normalized_name not in gained]

    report = resolve.ResolveReport(
        total_names=len(names),
        exact=exact,
        unmatched=[
            {"normalized_name": item.normalized_name, "occurrence": item.occurrence, "confidence": 0.0}
            for item in remaining
        ],
    )
    resolve.save_report(report, settings)
    print(f"재료명 {len(names)}종 / 정확 일치 {len(exact) - len(gained)}종 / 클러스터 전파 {len(gained)}종")

    if not remaining:
        print("LLM 매칭이 필요 없습니다. 바로 load 로 넘어가세요.")
        return 0

    # 남은 것 중 같은 클러스터끼리는 대표 하나만 물어봅니다.
    delegate = canonical.representatives(clusters, {item.normalized_name for item in remaining})
    heads = sorted({head for head in delegate.values()})
    ask = [by_name[key] for key in heads if key in by_name]
    print(canonical.render(clusters, unmatched=len(remaining), delegated=len(ask)))

    master = await resolve.fetch_master(settings)
    requests, key_map = resolve.build_requests(ask, master, settings=settings)
    runner = BatchRunner(STAGE_RESOLVE, settings)
    paths = runner.write_requests(requests, job_name=job_name)
    (runner.requests_dir / f"{job_name}_keys.json").write_text(
        json.dumps(key_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (runner.requests_dir / f"{job_name}_delegates.json").write_text(
        json.dumps(delegate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for path in paths:
        print(f"생성: {path.name} (마스터 {len(master)}행을 후보로 첨부)")
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    """3단계 진입점."""
    return asyncio.run(resolve_async(args.job, get_settings()))


def command_submit(args: argparse.Namespace) -> int:
    """단계별 배치 제출. --parts 로 몇 파트만 넣을 수 있습니다."""
    runner = BatchRunner(STAGE_NAMES[args.stage], get_settings())
    try:
        already = {job.batch_id for job in runner.load_manifest(args.job)}
    except FileNotFoundError:
        already = set()

    jobs = runner.submit(args.job, max_parts=args.parts if args.parts > 0 else None)
    fresh = [job for job in jobs if job.batch_id not in already]
    for job in fresh:
        print(f"제출: {job.input_file} -> {job.batch_id} ({job.status})")
    if not fresh:
        print("새로 제출할 파트가 없습니다.")

    remaining = len(runner.pending_parts(args.job))
    if remaining:
        print(f"남은 파트 {remaining}개. 이번 파트가 끝난 뒤 같은 명령을 다시 실행하세요.")
    return 0


def command_collect(args: argparse.Namespace) -> int:
    """단계별 결과 수거 + 중간 산출물 생성."""
    settings = get_settings()
    stage = STAGE_NAMES[args.stage]
    runner = BatchRunner(stage, settings)

    jobs = runner.wait(args.job, poll_seconds=args.poll) if args.wait else runner.refresh(args.job)
    for job in jobs:
        print(f"{job.batch_id}: {job.status}" + (f" ({job.error})" if job.error else ""))

    # 실패/만료/취소는 기다려도 달라지지 않습니다. "아직 안 끝남" 과 섞어 보고하면
    # 폴링 스크립트가 죽은 배치를 몇 시간이고 다시 물어보게 됩니다(실제로 한 번 겪었습니다).
    dead = [job for job in jobs if job.status in DEAD_STATUSES]
    running = [job for job in jobs if job.status not in TERMINAL_STATUSES]
    usable = [job for job in jobs if job.output_file_id]

    for job in dead:
        print(f"  종료됨({job.status}) {job.batch_id}: {job.error or '사유 없음'}", file=sys.stderr)

    # 파트를 나눠 돌리면 하나가 늦어도 나머지는 이미 결과가 있습니다. 그것까지 못 쓰게
    # 막으면 진행이 통째로 멈춥니다. 받을 수 있는 것은 받고, 못 받은 파트를 알려 줍니다.
    if not usable:
        if running:
            print("아직 완료된 파트가 없습니다.", file=sys.stderr)
            return 1
        print("결과를 가진 파트가 하나도 없습니다.", file=sys.stderr)
        return 2
    for path in runner.download(args.job):
        print(f"다운로드: {path.name}")

    if args.stage == "profile":
        profiles, failures, adjustments = profile.collect(args.job, settings=settings)
        print(f"\n{profile.render_profiles(profiles)}")
        print(f"\n{constraints.render(adjustments)}")
        print(f"\n프로파일 {len(profiles)}개 저장: {profile.profiles_path(settings).name}")
    elif args.stage == "extract":
        counts, failures = extract.collect(args.job, profiles=profile.load_profiles(settings), settings=settings)
        for dataset, count in sorted(counts.items()):
            print(f"  {dataset:32} {count}건")
    else:
        report = resolve.collect(args.job, report=resolve.load_report(settings), settings=settings)
        print(f"\n{report.render()}")
        failures = []

    for failure in failures[:20]:
        print(f"  실패 {failure}", file=sys.stderr)
    if failures:
        print(f"  ... 총 {len(failures)}건 실패", file=sys.stderr)

    if running:
        print(f"\n아직 도는 파트 {len(running)}개. 끝나면 같은 명령으로 다시 수거하세요.", file=sys.stderr)
        return 1
    if dead:
        return 2
    return 0


def command_load(args: argparse.Namespace) -> int:
    """중간 산출물을 staging 에 COPY 하고 타깃 테이블에 반영합니다."""
    settings = get_settings()
    rows = collect_rows(settings)
    if rows.is_empty():
        print("적재할 레코드가 없습니다. 2단계 산출물을 확인하세요.", file=sys.stderr)
        return 1
    report = asyncio.run(run_load(rows, settings=settings, truncate_staging_after=args.truncate_staging))
    print(report.render())
    return 0


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만듭니다."""
    parser = argparse.ArgumentParser(prog="data-pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-db", help="Neon 연결/스키마 점검")
    check.add_argument("--direct", action="store_true")
    check.set_defaults(func=command_check_db)

    inspect = sub.add_parser("inspect", help="raw 데이터셋 확인")
    inspect.add_argument("--raw", help="raw 경로. 생략하면 data_pipeline/data/raw")
    inspect.add_argument("--limit", type=int, default=2, help="데이터셋당 샘플 행 수")
    inspect.set_defaults(func=command_inspect)

    status = sub.add_parser("status", help="단계별 산출물 현황")
    status.set_defaults(func=command_status)

    prof = sub.add_parser("profile", help="1단계: 데이터셋 프로파일링 요청 생성")
    prof.add_argument("--job", required=True)
    prof.add_argument("--raw", help="raw 경로. 생략하면 data_pipeline/data/raw")
    prof.set_defaults(func=command_profile)

    ext = sub.add_parser("extract", help="2단계: 타깃 테이블 모양으로 추출 요청 생성")
    ext.add_argument("--job", required=True)
    ext.add_argument("--raw", help="raw 경로. 생략하면 data_pipeline/data/raw")
    ext.set_defaults(func=command_extract)

    res = sub.add_parser("resolve", help="3단계: 정확 일치 + 나머지 LLM 매칭 요청 생성")
    res.add_argument("--job", required=True)
    res.set_defaults(func=command_resolve)

    submit = sub.add_parser("submit", help="배치 제출")
    submit.add_argument("--stage", required=True, choices=sorted(STAGE_NAMES))
    submit.add_argument("--job", required=True)
    submit.add_argument(
        "--parts",
        type=int,
        default=1,
        help="이번에 제출할 파트 수 (기본 1). 대기 토큰 한도 때문에 나눠 넣습니다. 0 이면 전부",
    )
    submit.set_defaults(func=command_submit)

    collect = sub.add_parser("collect", help="결과 수거 + 중간 산출물 생성")
    collect.add_argument("--stage", required=True, choices=sorted(STAGE_NAMES))
    collect.add_argument("--job", required=True)
    collect.add_argument("--wait", action="store_true", help="완료될 때까지 폴링")
    collect.add_argument("--poll", type=int, default=60, help="폴링 간격(초)")
    collect.set_defaults(func=command_collect)

    load = sub.add_parser("load", help="staging -> 타깃 테이블 적재")
    load.add_argument("--truncate-staging", action="store_true", help="적재 후 staging 비우기")
    load.set_defaults(func=command_load)

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
