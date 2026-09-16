"""rag_lab CLI.

uv run rag-lab check-db
uv run rag-lab ask "김치로 뭐 해먹지"
uv run rag-lab experiment --name topk5 --cases rag_lab/experiments/_template/questions.jsonl
uv run rag-lab recommend --name baseline --cases rag_lab/experiments/<id>/cases.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rag_lab.clients import LlmChatClient, LlmEmbeddingClient, LlmJudgeClient
from rag_lab.config import get_settings
from rag_lab.db import check_connection, engine_scope
from rag_lab.experiment import load_cases, run_experiment
from rag_lab.graph import RagDependencies, make_ask, route_question
from rag_lab.recommendation import (
    RUBRIC_ITEMS,
    load_reasons,
    load_recommendation_cases,
    rescore_experiment,
    run_reason_experiment,
)


def command_check_db(args: argparse.Namespace) -> int:
    """Neon 연결과 스키마 상태를 점검합니다."""
    report = asyncio.run(check_connection(direct=args.direct))
    print(report.render())
    return 0 if report.ok else 1


def command_route(args: argparse.Namespace) -> int:
    """라우팅 결과만 확인합니다. DB/API 없이 됩니다."""
    print(route_question(args.question))
    return 0


async def ask_once(question: str) -> int:
    """질문 하나를 그래프에 태웁니다."""
    settings = get_settings()
    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            deps = RagDependencies(
                conn=conn,
                embedder=LlmEmbeddingClient(settings),
                chat=LlmChatClient(settings),
                settings=settings,
            )
            state = await make_ask(deps)(question)

    print(f"route : {state.get('route')}")
    print(f"근거  : {len(state.get('docs', []))}건")
    for doc in state.get("docs", []):
        print(f"  {doc.score:.3f}  {doc.as_context()}")
    print(f"\n{state.get('answer', '')}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    """질문 하나에 답합니다."""
    return asyncio.run(ask_once(args.question))


async def run_experiment_command(name: str, cases_path: Path, out_dir: Path | None) -> int:
    """질문 세트로 실험을 한 번 돌리고 결과를 저장합니다."""
    settings = get_settings()
    cases = load_cases(cases_path)

    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            deps = RagDependencies(
                conn=conn,
                embedder=LlmEmbeddingClient(settings),
                chat=LlmChatClient(settings),
                settings=settings,
            )
            report = await run_experiment(name, cases, deps, settings=settings)

    print(report.render())
    target = out_dir or (settings.owner_dir / "results")
    print(f"\n결과 저장: {report.write_jsonl(target)}")
    return 0


def command_experiment(args: argparse.Namespace) -> int:
    """실험을 실행합니다."""
    out_dir = Path(args.out) if args.out else None
    return asyncio.run(run_experiment_command(args.name, Path(args.cases), out_dir))


async def run_reason_command(
    name: str, cases_path: Path, out_dir: Path | None, *, judge: bool, rescore: Path | None = None
) -> int:
    """상황 세트로 추천 문구를 만들고 검사합니다.

    **DB 를 열지 않습니다.** 추천 문구의 입력은 `my_recipe_candidates` 가 이미 준
    구조화된 값이라 벡터 검색을 돌 이유가 없습니다(`recommendation.py` 머리말 참고).
    그래서 `experiment` 와 달리 엔진 없이 LLM 클라이언트만 있으면 됩니다.
    """
    settings = get_settings()
    cases = load_recommendation_cases(cases_path)
    chat = LlmChatClient(settings)

    # 판정자는 **생성과 다른 모델**입니다. `JUDGE_MODEL` 이 비었거나 생성 모델과
    # 같으면 여기서 실패합니다 - 자기 답을 자기가 채점하면 점수를 믿을 수 없습니다
    # (`ai_context/사람이 쓴 문서/rag 평가 지표 종류.md` 5.2).
    scorer = LlmJudgeClient(settings) if judge else None
    if scorer is not None:
        print(f"판정 모델: {scorer.provider.name} / {scorer.model}\n")

    if rescore is not None:
        # 판정자 교차 검증. 문구를 새로 만들지 않고 지난 결과를 다시 채점만 합니다.
        if scorer is None:
            raise RuntimeError("--rescore 는 --judge 와 함께 써야 합니다. 채점만 하는 명령입니다.")
        report = await rescore_experiment(name, cases, load_reasons(rescore), judge=scorer, settings=settings)
    else:
        report = await run_reason_experiment(name, cases, chat=chat, judge=scorer, settings=settings)

    print(report.render())
    print()
    for result in report.results:
        mark = "OK" if result.checks_passed else "X "
        score = ""
        if result.rubric is not None:
            mean = result.rubric.mean
            score = f"[{mean:.1f}]" if mean is not None else "[채점실패]"
        print(f"  {mark} {score:>7} {result.case_id:24} {result.reason}")
        for check in result.checks:
            if not check.passed:
                print(f"            - {check.name}: {check.detail}")
        if result.rubric is not None and result.rubric.passed is False:
            labels = {key: label for key, label, _ in RUBRIC_ITEMS}
            weakest = result.rubric.weakest
            print(f"            - 최저 {labels[weakest]} {result.rubric.scores[weakest]}점: {result.rubric.comment}")
        elif result.rubric is not None and result.rubric.error:
            print(f"            - {result.rubric.error}")

    target = out_dir or (settings.owner_dir / "results")
    print(f"\n결과 저장: {report.write_jsonl(target)}")
    return 0


def command_recommend(args: argparse.Namespace) -> int:
    """추천 문구 실험을 실행합니다."""
    out_dir = Path(args.out) if args.out else None
    rescore = Path(args.rescore) if args.rescore else None
    return asyncio.run(run_reason_command(args.name, Path(args.cases), out_dir, judge=args.judge, rescore=rescore))


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 만듭니다."""
    parser = argparse.ArgumentParser(prog="rag-lab", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-db", help="Neon 연결/스키마 점검")
    check.add_argument("--direct", action="store_true")
    check.set_defaults(func=command_check_db)

    route = sub.add_parser("route", help="라우팅 결과만 확인 (DB/API 불필요)")
    route.add_argument("question")
    route.set_defaults(func=command_route)

    ask = sub.add_parser("ask", help="질문 하나에 답하기")
    ask.add_argument("question")
    ask.set_defaults(func=command_ask)

    experiment = sub.add_parser("experiment", help="질문 세트로 실험 실행")
    experiment.add_argument("--name", required=True, help="실험 이름 (결과 파일명에 들어갑니다)")
    experiment.add_argument("--cases", required=True, help="질문 세트 JSONL 경로")
    experiment.add_argument("--out", help="결과를 저장할 디렉터리 (기본: experiments/<owner>/results)")
    experiment.set_defaults(func=command_experiment)

    recommend = sub.add_parser("recommend", help="상황 세트로 추천 문구 실험 (DB 불필요)")
    recommend.add_argument("--name", required=True, help="실험 이름 (결과 파일명에 들어갑니다)")
    recommend.add_argument("--cases", required=True, help="상황 세트 JSONL 경로")
    recommend.add_argument("--out", help="결과를 저장할 디렉터리 (기본: experiments/<owner>/results)")
    recommend.add_argument("--judge", action="store_true", help="루브릭 채점까지 (JUDGE_MODEL 필요, 호출 2배)")
    recommend.add_argument("--rescore", help="지난 결과 JSONL 의 문구를 다시 채점만 (판정자 교차 검증)")
    recommend.set_defaults(func=command_recommend)

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
