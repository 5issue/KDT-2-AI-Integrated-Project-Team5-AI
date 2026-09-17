"""**실험 실행·기록·재채점** 검증. DB 도 API 도 쓰지 않습니다.

`rag_lab.recommendation.experiment` — 서빙 경로가 아니라 `rag-lab recommend` 용입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reco_helpers import CASES_DIR, case, rubric_json

from rag_lab.recommendation.experiment import (
    load_reasons,
    load_recommendation_cases,
    rescore_experiment,
    run_reason_experiment,
)
from rag_lab.recommendation.judge import PASS_MEAN_SCORE
from rag_lab.testing import FakeChatClient


@pytest.mark.asyncio
async def test_identical_reasons_lower_the_distinct_ratio() -> None:
    """20~30개 문구가 전부 같으면 틀에 박힌 문장을 찍어내고 있는 것입니다(계획 4-3)."""
    cases = [case(case_id="a"), case(case_id="b"), case(case_id="c")]
    report = await run_reason_experiment("t", cases, chat=FakeChatClient("같은 문구입니다."))

    assert report.distinct_ratio == pytest.approx(1 / 3)
    assert len(report.results) == 3


@pytest.mark.asyncio
async def test_report_counts_which_check_failed(tmp_path: Path) -> None:
    cases = [case(case_id="a"), case(case_id="b")]
    report = await run_reason_experiment("t", cases, chat=FakeChatClient("참기름이 있어서 20분이면 됩니다."))

    assert report.check_pass_rate == 0.0
    assert report.failure_counts == {"보유_뒤집힘": 2, "조리시간_지어냄": 2}

    # 결과 파일 첫 줄은 메타데이터. 질문 세트 실험과 같은 규칙입니다.
    path = report.write_jsonl(tmp_path)
    meta = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert meta["kind"] == "recommendation_reason"
    assert "api_key" not in json.dumps(meta).lower(), "자격증명이 결과에 남으면 안 됩니다"


@pytest.mark.asyncio
async def test_judge_is_skipped_unless_requested() -> None:
    """채점은 케이스당 LLM 호출이 한 번 더 늘어납니다. 기본값은 안 부르는 것입니다."""
    report = await run_reason_experiment("t", [case()], chat=FakeChatClient("문구"))
    assert report.judge_pass_rate is None
    assert report.results[0].rubric is None


# --- 상황 세트 파일 ----------------------------------------------------------


def test_case_file_loads_and_covers_the_required_edges() -> None:
    """계획 4-2 가 요구한 경계 사례가 실제로 파일에 있는지 봅니다."""
    cases = load_recommendation_cases(CASES_DIR / "cases.jsonl")

    assert 20 <= len(cases) <= 30, "20~30개면 충분합니다(계획 4-2)"
    assert any(not c.missing for c in cases), "부족 재료 0개"
    assert any(len(c.missing) == 1 for c in cases), "부족 재료 1개"
    assert any(len(c.missing) >= 3 for c in cases), "부족 재료 3개 이상"
    assert any(c.missing and set(c.missing) <= set(c.pantry) for c in cases), "상비재료만 부족"
    assert any(c.cook_time_min is None for c in cases), "조리시간 없음"
    assert any(c.cook_time_min is not None for c in cases), "조리시간 있음"
    assert any(not c.have for c in cases), "빈 냉장고"

    # 같은 재료가 여러 레시피에 걸리는 묶음. 문구가 전부 같아지는지 보는 용도입니다.
    with_spinach = [c for c in cases if "시금치" in c.known_ingredients]
    assert len({c.recipe for c in with_spinach}) >= 3


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    """id 가 겹치면 결과에서 어느 케이스였는지 구분이 안 됩니다."""
    path = tmp_path / "cases.jsonl"
    line = json.dumps({"case_id": "dup", "recipe": "가", "match_rate": 1.0}, ensure_ascii=False)
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="중복"):
        load_recommendation_cases(path)


@pytest.mark.asyncio
async def test_report_separates_scoring_failures_from_low_scores() -> None:
    report = await run_reason_experiment(
        "t",
        [case(case_id="a"), case(case_id="b")],
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient("판정 불가"),
    )

    assert report.unscored == 2
    assert report.mean_score is None
    assert report.judge_pass_rate is None, "채점 실패를 불합격으로 세면 안 됩니다"


@pytest.mark.asyncio
async def test_report_carries_item_means(tmp_path: Path) -> None:
    """어느 축이 약한지가 결과 파일에 남아야 나중에 비교할 수 있습니다."""
    report = await run_reason_experiment(
        "t",
        [case(case_id="a")],
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient(rubric_json(9, compliance=3)),
    )

    assert report.item_means["compliance"] == pytest.approx(3.0)
    meta = json.loads(report.write_jsonl(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert meta["item_means"]["compliance"] == 3.0
    assert meta["params"]["pass_mean_score"] == PASS_MEAN_SCORE


@pytest.mark.asyncio
async def test_rescore_does_not_regenerate(tmp_path: Path) -> None:
    """판정자를 비교하려면 **같은 문구**를 다시 재야 합니다.

    새로 생성하면 문구 차이와 판정자 차이가 섞여 무엇 때문에 점수가 바뀌었는지
    알 수 없습니다.
    """
    cases = [case(case_id="a"), case(case_id="b")]
    first = await run_reason_experiment(
        "first",
        cases,
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient(rubric_json()),
    )
    path = first.write_jsonl(tmp_path)

    judge = FakeChatClient(rubric_json(6))
    again = await rescore_experiment("again", cases, load_reasons(path), judge=judge)

    assert [r.reason for r in again.results] == [r.reason for r in first.results]
    assert again.mean_score == pytest.approx(6.0)
    assert again.params["rescored"] is True


def test_load_reasons_skips_the_metadata_line(tmp_path: Path) -> None:
    """결과 파일 첫 줄은 메타데이터입니다. 케이스로 세면 개수가 하나 늘어납니다."""
    path = tmp_path / "r.jsonl"
    meta = json.dumps({"name": "x", "params": {}}, ensure_ascii=False)
    row = json.dumps({"case_id": "a", "reason": "문구"}, ensure_ascii=False)
    path.write_text(f"{meta}\n{row}\n", encoding="utf-8")

    prior = load_reasons(path)
    assert set(prior) == {"a"}
    assert prior["a"].reason == "문구"


@pytest.mark.asyncio
async def test_rescore_rejects_a_case_missing_from_the_previous_run(tmp_path: Path) -> None:
    """상황 세트를 고친 뒤 옛 결과로 재채점하면 조용히 일부만 채점됩니다."""
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"case_id": "a", "reason": "문구"}, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="지난 결과에 없는"):
        await rescore_experiment(
            "x", [case(case_id="a"), case(case_id="b")], load_reasons(path), judge=FakeChatClient(rubric_json())
        )


# --- 코드래빗 리뷰 반영 (#14) ------------------------------------------------


@pytest.mark.asyncio
async def test_rescore_rejects_a_case_whose_content_changed(tmp_path: Path) -> None:
    """id 가 같아도 상황이 바뀌었으면 옛 문구를 새 상황으로 채점하게 됩니다."""
    original = [case(case_id="a")]
    first = await run_reason_experiment(
        "first",
        original,
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient(rubric_json()),
    )
    path = first.write_jsonl(tmp_path)

    edited = [case(case_id="a", missing=["참기름", "간장", "설탕"])]
    with pytest.raises(ValueError, match="상황이 바뀐"):
        await rescore_experiment("again", edited, load_reasons(path), judge=FakeChatClient(rubric_json()))


@pytest.mark.asyncio
async def test_rescore_records_whether_it_could_verify(tmp_path: Path) -> None:
    """옛 결과 파일에는 지문이 없습니다. 확인 못 했다는 사실을 기록에 남깁니다."""
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"case_id": "a", "reason": "옛 문구"}, ensure_ascii=False) + "\n", encoding="utf-8")

    report = await rescore_experiment("x", [case(case_id="a")], load_reasons(path), judge=FakeChatClient(rubric_json()))
    assert report.params["case_hash_verified"] is False


@pytest.mark.asyncio
async def test_unverified_rescore_does_not_mint_a_fingerprint(tmp_path: Path) -> None:
    """지문 없는 옛 결과를 재채점할 때 현재 케이스 지문을 찍으면 안 됩니다.

    그 결과를 또 재채점하면 그 값이 "원본 지문" 으로 읽혀, 확인한 적 없는 문구가
    검증된 것으로 둔갑합니다.
    """
    old = tmp_path / "old.jsonl"
    old.write_text(json.dumps({"case_id": "a", "reason": "옛 문구"}, ensure_ascii=False) + "\n", encoding="utf-8")

    first = await rescore_experiment("x", [case(case_id="a")], load_reasons(old), judge=FakeChatClient(rubric_json()))
    assert first.params["case_hash_verified"] is False
    assert first.results[0].case_hash == "", "확인 못 한 건에 지문을 찍으면 안 됩니다"

    # 그 결과를 다시 재채점해도 여전히 미검증이어야 합니다.
    again = await rescore_experiment(
        "y",
        [case(case_id="a")],
        load_reasons(first.write_jsonl(tmp_path)),
        judge=FakeChatClient(rubric_json()),
    )
    assert again.params["case_hash_verified"] is False, "미검증이 세탁되면 안 됩니다"


# --- 실행 기록의 정직성 (코드래빗 4차) ---------------------------------------


@pytest.mark.asyncio
async def test_result_records_the_client_that_actually_ran(tmp_path: Path) -> None:
    """클라이언트는 밖에서 주입받습니다. 설정값을 적으면 돌지도 않은 모델 이름이 남습니다.

    판정자 비교가 이 이름에 기대고 있어서, 틀리면 비교 자체가 무의미해집니다.
    """
    report = await run_reason_experiment(
        "t",
        [case(case_id="a")],
        chat=FakeChatClient("문구", model_id="fake/generator"),
        judge=FakeChatClient(rubric_json(), model_id="fake/scorer"),
    )

    assert report.params["chat_model"] == "fake/generator"
    assert report.params["judge_model"] == "fake/scorer"

    meta = json.loads(report.write_jsonl(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert meta["params"]["chat_model"] == "fake/generator"


@pytest.mark.asyncio
async def test_rescore_leaves_the_generation_model_empty(tmp_path: Path) -> None:
    """재채점은 생성을 하지 않습니다. 설정값을 적으면 그 모델이 돈 것처럼 읽힙니다."""
    prior = tmp_path / "prior.jsonl"
    prior.write_text(json.dumps({"case_id": "a", "reason": "문구"}, ensure_ascii=False) + "\n", encoding="utf-8")

    report = await rescore_experiment(
        "t", [case(case_id="a")], load_reasons(prior), judge=FakeChatClient(rubric_json(), model_id="fake/scorer")
    )

    assert report.params["chat_model"] is None
    assert report.params["judge_model"] == "fake/scorer"
