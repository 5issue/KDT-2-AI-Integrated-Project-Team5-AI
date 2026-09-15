"""실험 실행/기록 테스트. 가짜 클라이언트로 API 없이 돕니다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rag_lab.config import Settings
from rag_lab.experiment import (
    ExperimentReport,
    QuestionCase,
    load_cases,
    run_experiment,
    snapshot_params,
)
from rag_lab.graph import RagDependencies
from rag_lab.retrieval import RetrievedDoc
from rag_lab.testing import FakeChatClient, FakeEmbeddingClient

RECIPE_DOC = RetrievedDoc(source="recipe", doc_id=12, title="김치찌개", body="", distance=0.1, score=0.9)


@pytest.fixture
def deps(fake_embedder: FakeEmbeddingClient, fake_chat: FakeChatClient, offline_settings: Settings) -> RagDependencies:
    """DB 없이 도는 의존성 묶음."""
    return RagDependencies(conn=None, embedder=fake_embedder, chat=fake_chat, settings=offline_settings)  # type: ignore[arg-type]


async def test_report_scores_routing_and_hits(
    monkeypatch: pytest.MonkeyPatch, deps: RagDependencies, offline_settings: Settings
) -> None:
    """기대값이 있는 케이스만 정확도 계산에 들어갑니다."""

    async def fake_search(*_args: Any, **_kwargs: Any) -> list[RetrievedDoc]:
        return [RECIPE_DOC]

    monkeypatch.setattr("rag_lab.graph.search", fake_search)

    cases = [
        QuestionCase("김치로 뭐 해먹지", expected_route="recipe", expected_doc_ids=[12]),
        # 라우터는 recipe 로 보내는 질문에 일부러 product 를 기대값으로 달아 불일치를 만듭니다.
        QuestionCase("간단한 한식 알려줘", expected_route="product", expected_doc_ids=[99]),
        QuestionCase("아무 질문"),
    ]
    report = await run_experiment("unit", cases, deps, settings=offline_settings)

    assert len(report.results) == 3
    assert report.route_accuracy == pytest.approx(0.5)  # 1번은 일치, 2번은 불일치
    assert report.hit_rate == pytest.approx(0.5)  # 12 는 적중, 99 는 실패
    assert report.mean_latency_ms > 0


def test_snapshot_params_excludes_secrets() -> None:
    """실험 기록에 자격증명이 섞여 들어가면 안 됩니다."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    params = snapshot_params(settings)

    assert "top_k" in params
    assert not {"database_url", "database_url_direct", "llm_api_key", "llm_embedding_api_key"} & set(params)


def test_write_jsonl_round_trip(tmp_path: Path) -> None:
    """첫 줄 메타데이터 + 케이스별 한 줄 형식으로 저장됩니다."""
    report = ExperimentReport(name="unit", started_at="2026-09-07T00:00:00+00:00", params={"top_k": 5})
    path = report.write_jsonl(tmp_path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["name"] == "unit"
    assert lines[0]["params"] == {"top_k": 5}


def test_load_cases_reads_template_questions() -> None:
    """예시 질문 세트가 읽히는지 확인합니다."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    cases = load_cases(settings.experiments_dir / "_template" / "questions.jsonl")

    assert len(cases) == 5
    assert {case.expected_route for case in cases} == {"recipe", "product", "ingredient"}


def test_load_cases_rejects_empty_file(tmp_path: Path) -> None:
    """빈 질문 세트로 실험이 돌아가 버리지 않게 막습니다."""
    path = tmp_path / "empty.jsonl"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="비어 있습니다"):
        load_cases(path)
