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

## 이 파일이 하는 일은 둘뿐입니다

인자를 읽고(`build_parser`), 도메인 모듈을 부르고 종료코드를 돌려주는 것입니다.
**명령 본체는 여기 두지 않습니다.** 적재 흐름은 그 데이터를 아는 모듈에 있습니다.

    resolve        -> stages.resolve.run_resolve
    sync-master    -> load.ingredient_master.run_sync_master
    load-catalog   -> load.catalog.run_load_catalog
    load-recipes   -> load.recipe_catalog.run_load_recipes
    load           -> load.bulk_insert.run_load
    embed          -> load.embedding.run_embedding
    seed-demo      -> load.demo_seed.run_demo_seed

예외는 `submit` 과 `collect` 입니다. 둘은 `--stage` 로 세 단계에 나눠 보내는
디스패치라 어느 한 도메인에 속하지 않습니다.

여기 남는 것은 **의존성 조립**입니다. `load-catalog` 와 `load-recipes` 는 재료 마스터
조회가 필요한데 그 조회는 `stages.resolve` 에 있습니다. 적재 모듈이 단계 모듈을 직접
부르면 계층이 거꾸로 물리므로, 조회를 **함수로 넘겨 줍니다**(`load_lookup`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from data_pipeline.batch.client import DEAD_STATUSES, TERMINAL_STATUSES, BatchRunner
from data_pipeline.batch.raw_source import preview_raw_source
from data_pipeline.config import Settings, get_settings
from data_pipeline.db import check_connection
from data_pipeline.load import catalog, ingredient_master, recipe_catalog
from data_pipeline.load.bulk_insert import (
    collect_rows,
    run_load,
)
from data_pipeline.load.demo_scenario import run_demo_scenario
from data_pipeline.load.demo_seed import run_demo_seed
from data_pipeline.load.embedding import TARGETS as EMBEDDING_TARGETS
from data_pipeline.load.embedding import run_embedding
from data_pipeline.stages import (
    STAGE_EXTRACT,
    STAGE_PROFILE,
    STAGE_RESOLVE,
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


def command_resolve(args: argparse.Namespace) -> int:
    """3단계 진입점."""
    return asyncio.run(resolve.run_resolve(args.job, get_settings()))


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
        raw_path = Path(args.raw) if getattr(args, "raw", None) else None
        profiles, failures, adjustments = profile.collect(args.job, settings=settings, raw_path=raw_path)
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


def command_sync_master(args: argparse.Namespace) -> int:
    """마스터 보강 진입점."""
    return asyncio.run(ingredient_master.run_sync_master(get_settings(), apply=args.apply))


def command_load_catalog(args: argparse.Namespace) -> int:
    """카탈로그 적재 진입점. 재료 마스터 조회를 주입합니다."""
    settings = get_settings()
    return asyncio.run(
        catalog.run_load_catalog(settings, apply=args.apply, load_lookup=lambda: resolve.fetch_match_lookup(settings))
    )


def command_embed(args: argparse.Namespace) -> int:
    """embedding 컬럼을 채웁니다. rag_lab 의 pgvector 검색이 여기에 의존합니다."""
    settings = get_settings()
    targets = list(EMBEDDING_TARGETS) if args.target == "all" else [args.target]
    report = asyncio.run(run_embedding(targets, settings=settings, dry_run=not args.apply, refresh=args.refresh))
    print(report.render())
    if not args.apply:
        print("\n실제로 채우려면 --apply 를 붙이세요.", file=sys.stderr)
    return 0


async def recipe_lookup(settings: Settings) -> dict[str, int]:
    """마스터 조회에 3단계 매칭 결과를 얹습니다.

    3단계를 이미 돌렸다면 그 결과를 얹습니다. 마스터 정확 일치로 안 붙던 이름
    (`후춧가루`, `닭가슴살` 같은 것)이 여기서 붙습니다.

    **조립은 CLI 가 합니다.** 적재 모듈이 `stages.resolve` 를 직접 부르면 계층이
    거꾸로 물립니다. 어느 조회를 쓸지 정하는 것은 부르는 쪽의 몫입니다.
    """
    lookup = await resolve.fetch_match_lookup(settings)
    try:
        llm_matches = resolve.load_matches(settings)
    except FileNotFoundError:
        llm_matches = {}
    else:
        print(f"3단계 매칭 {len(llm_matches)}종을 함께 씁니다.")
    return {**lookup, **llm_matches}


def command_load_recipes(args: argparse.Namespace) -> int:
    """레시피 적재 진입점. 마스터 + 3단계 매칭 조회를 주입합니다."""
    settings = get_settings()
    return asyncio.run(
        recipe_catalog.run_load_recipes(settings, apply=args.apply, load_lookup=lambda: recipe_lookup(settings))
    )


def command_seed_demo(args: argparse.Namespace) -> int:
    """데모 사용자·냉장고·구매이력·인기도를 넣습니다."""
    report = asyncio.run(
        run_demo_seed(
            users=args.users,
            settings=get_settings(),
            dry_run=not args.apply,
            reset_stock=args.reset_stock,
        )
    )
    print(report.render())
    if not args.apply:
        print("\n실제로 넣으려면 --apply 를 붙이세요.", file=sys.stderr)
    return 0


def command_seed_scenario(args: argparse.Namespace) -> int:
    """데모 시나리오를 설정대로 맞추고 검증 결과를 보여줍니다."""
    report = asyncio.run(
        run_demo_scenario(settings=get_settings(), dry_run=not args.apply, rollback_path=args.rollback_sql)
    )
    print(report.render())
    if not args.apply:
        print("\n실제로 넣으려면 --apply 를 붙이세요.", file=sys.stderr)
    if not report.ok:
        print("\n검증에 실패한 항목이 있습니다.", file=sys.stderr)
        return 1
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
    # profile/extract 와 같은 raw 경로를 받아야 합니다. collect 만 기본 경로로 다시
    # 훑으면, --raw 로 돌린 프로파일의 companion 연결이 제약 레이어에서 통째로 지워집니다.
    collect.add_argument("--raw", help="raw 경로. 생략하면 data_pipeline/data/raw")
    collect.set_defaults(func=command_collect)

    master = sub.add_parser("sync-master", help="공공 영양성분 데이터로 재료 마스터 보강")
    master.add_argument("--apply", action="store_true", help="실제로 DB 에 반영 (없으면 집계만)")
    master.set_defaults(func=command_sync_master)

    cat = sub.add_parser("load-catalog", help="category / product / product_ingredient 적재")
    cat.add_argument("--apply", action="store_true", help="실제로 DB 에 반영 (없으면 집계만)")
    cat.set_defaults(func=command_load_catalog)

    embed = sub.add_parser("embed", help="recipe / product / ingredient 의 embedding 채우기")
    embed.add_argument("--target", default="all", choices=["all", *EMBEDDING_TARGETS])
    embed.add_argument("--apply", action="store_true", help="실제로 API 를 부르고 DB 에 반영")
    embed.add_argument(
        "--refresh",
        action="store_true",
        help="이미 채워진 행도 다시 만듭니다 (재료 연결이 바뀐 뒤)",
    )
    embed.set_defaults(func=command_embed)

    recipes = sub.add_parser("load-recipes", help="구조화된 한국어 레시피(COOKRCP01) 적재")
    recipes.add_argument("--apply", action="store_true", help="실제로 DB 에 반영")
    recipes.set_defaults(func=command_load_recipes)

    demo = sub.add_parser("seed-demo", help="데모 사용자/냉장고/구매이력/인기도 시드")
    demo.add_argument("--users", type=int, default=20, help="만들 사용자 수")
    demo.add_argument("--apply", action="store_true", help="실제로 DB 에 반영")
    demo.add_argument(
        "--reset-stock",
        action="store_true",
        help="표식 없이 이미 채워진 재고까지 덮어씀 (기본은 NULL 이거나 seed-demo 가 쓴 행만)",
    )
    demo.set_defaults(func=command_seed_demo)

    scenario = sub.add_parser("seed-scenario", help="데모 시나리오(사용자/냉장고/대표 상품) 고정")
    scenario.add_argument("--apply", action="store_true", help="실제로 DB 에 반영")
    scenario.add_argument(
        "--rollback-sql",
        type=Path,
        default=None,
        help="바꾸기 전 상태로 되돌리는 SQL 을 남길 경로",
    )
    scenario.set_defaults(func=command_seed_scenario)

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
