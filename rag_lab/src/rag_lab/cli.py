"""rag_lab CLI.

uv run rag-lab check-db
uv run rag-lab ask "김치로 뭐 해먹지"
uv run rag-lab experiment --name topk5 --cases rag_lab/experiments/_template/questions.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rag_lab.clients import LlmChatClient, LlmEmbeddingClient
from rag_lab.config import get_settings
from rag_lab.db import check_connection, engine_scope
from rag_lab.experiment import load_cases, run_experiment
from rag_lab.graph import RagDependencies, make_ask, route_question


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
