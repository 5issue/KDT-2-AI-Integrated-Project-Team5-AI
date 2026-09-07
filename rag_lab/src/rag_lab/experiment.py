"""실험 실행과 기록.

같은 질문 세트를 파라미터만 바꿔 반복해서 돌리고, 결과를 JSONL 로 남겨 비교합니다.
설정을 함께 기록하기 때문에 "어떤 top_k 로 돌린 결과였는지" 를 나중에 되짚을 수 있습니다.
API 키나 DB URL 은 기록에 남기지 않습니다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rag_lab.config import Settings, get_settings
from rag_lab.graph import RagDependencies, make_ask


@dataclass(slots=True)
class QuestionCase:
    """평가할 질문 하나."""

    question: str
    expected_route: str | None = None
    expected_doc_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class CaseResult:
    """질문 하나의 실행 결과."""

    question: str
    route: str
    answer: str
    retrieved_ids: list[int]
    top_score: float | None
    elapsed_ms: float
    route_ok: bool | None = None
    hit: bool | None = None


@dataclass(slots=True)
class ExperimentReport:
    """실험 한 번의 결과 전체."""

    name: str
    started_at: str
    params: dict[str, object]
    results: list[CaseResult] = field(default_factory=list)

    @property
    def route_accuracy(self) -> float | None:
        """expected_route 가 있는 케이스에서의 라우팅 정확도."""
        judged = [result.route_ok for result in self.results if result.route_ok is not None]
        return sum(judged) / len(judged) if judged else None

    @property
    def hit_rate(self) -> float | None:
        """expected_doc_ids 가 있는 케이스에서 정답 문서를 하나라도 건진 비율."""
        judged = [result.hit for result in self.results if result.hit is not None]
        return sum(judged) / len(judged) if judged else None

    @property
    def mean_latency_ms(self) -> float:
        """평균 응답 시간."""
        return sum(result.elapsed_ms for result in self.results) / len(self.results) if self.results else 0.0

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [
            f"실험      : {self.name}",
            f"파라미터  : {self.params}",
            f"케이스    : {len(self.results)}건",
            f"평균 지연 : {self.mean_latency_ms:.0f} ms",
        ]
        if (accuracy := self.route_accuracy) is not None:
            lines.append(f"라우팅    : {accuracy:.1%}")
        if (hit := self.hit_rate) is not None:
            lines.append(f"검색 적중 : {hit:.1%}")
        return "\n".join(lines)

    def write_jsonl(self, directory: Path) -> Path:
        """결과를 JSONL 로 저장합니다. 첫 줄은 메타데이터입니다."""
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.replace(":", "").replace("-", "")
        path = directory / f"{stamp}_{self.name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            meta = {"name": self.name, "started_at": self.started_at, "params": self.params}
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for result in self.results:
                handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        return path


def snapshot_params(settings: Settings) -> dict[str, object]:
    """실험에 영향을 주는 설정만 뽑습니다. 자격증명은 담지 않습니다."""
    return {
        "chat_model": settings.openai_chat_model,
        "embedding_model": settings.openai_embedding_model,
        "embedding_dim": settings.embedding_dim,
        "top_k": settings.top_k,
        "score_threshold": settings.score_threshold,
        "distance_metric": settings.distance_metric,
        "neon_branch": settings.neon_branch,
    }


async def run_experiment(
    name: str,
    cases: Sequence[QuestionCase],
    deps: RagDependencies,
    *,
    settings: Settings | None = None,
) -> ExperimentReport:
    """질문 세트를 한 번 돌리고 결과를 모읍니다."""
    settings = settings or get_settings()
    ask = make_ask(deps)
    report = ExperimentReport(
        name=name,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        params=snapshot_params(settings),
    )

    for case in cases:
        started = time.perf_counter()
        state = await ask(case.question)
        elapsed_ms = (time.perf_counter() - started) * 1000

        docs = state.get("docs", [])
        retrieved_ids = [doc.doc_id for doc in docs]
        route = str(state.get("route", ""))

        report.results.append(
            CaseResult(
                question=case.question,
                route=route,
                answer=state.get("answer", ""),
                retrieved_ids=retrieved_ids,
                top_score=docs[0].score if docs else None,
                elapsed_ms=elapsed_ms,
                route_ok=(route == case.expected_route) if case.expected_route else None,
                hit=bool(set(retrieved_ids) & set(case.expected_doc_ids)) if case.expected_doc_ids else None,
            )
        )
    return report


def load_cases(path: Path) -> list[QuestionCase]:
    """질문 세트 JSONL 을 읽습니다."""
    cases: list[QuestionCase] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_no} JSON 파싱 실패: {exc.msg}") from exc
            cases.append(
                QuestionCase(
                    question=str(payload["question"]),
                    expected_route=payload.get("expected_route"),
                    expected_doc_ids=[int(value) for value in payload.get("expected_doc_ids", [])],
                )
            )
    if not cases:
        raise ValueError(f"질문 세트가 비어 있습니다: {path}")
    return cases
