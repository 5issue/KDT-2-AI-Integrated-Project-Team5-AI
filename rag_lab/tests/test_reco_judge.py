"""**루브릭 채점** 검증. 실제 판정자를 부르지 않습니다.

`rag_lab.recommendation.judge` — 항목은
`ai_context/사람이 쓴 문서/rag 평가 지표 종류.md` 7절, 통과선은 평균 7.5점입니다.
"""

from __future__ import annotations

import json

import pytest
from reco_helpers import case, rubric_json

from rag_lab.recommendation.judge import PASS_MEAN_SCORE, RUBRIC_ITEMS, parse_rubric, score_reason
from rag_lab.testing import FakeChatClient


def test_rubric_has_the_eight_items_from_the_team_document() -> None:
    """항목이 바뀌면 지난 실험과 점수를 비교할 수 없습니다. 목록을 고정합니다."""
    keys = [key for key, _, _ in RUBRIC_ITEMS]
    assert keys == [
        "goal",
        "brand",
        "differentiation",
        "clarity",
        "attractiveness",
        "relevance",
        "compliance",
        "brevity",
    ]


def test_mean_at_the_threshold_passes() -> None:
    """통과선은 평균 7.5 입니다. 경계값이 어느 쪽인지 못박아 둡니다."""
    assert parse_rubric(rubric_json(8, goal=7)).mean == pytest.approx(7.875)
    assert parse_rubric(rubric_json(8, goal=7)).passed

    # 8개 중 넷이 10점, 넷이 5점이면 합 60 / 8 = 7.5 입니다.
    borderline = parse_rubric(rubric_json(5, goal=10, brand=10, differentiation=10, clarity=10))
    assert borderline.mean == pytest.approx(PASS_MEAN_SCORE)
    assert borderline.passed, "정확히 7.5 는 통과입니다"


def test_mean_below_the_threshold_fails() -> None:
    assert not parse_rubric(rubric_json(7)).passed


def test_weakest_item_points_at_what_to_fix() -> None:
    """총점만 보면 프롬프트를 어디로 고칠지 모릅니다. 쪼갠 이유가 이것입니다."""
    assert parse_rubric(rubric_json(9, compliance=2)).weakest == "compliance"


def test_scores_out_of_range_are_clamped() -> None:
    """12점을 그대로 받으면 평균이 부풀어 통과선이 의미를 잃습니다."""
    score = parse_rubric(rubric_json(8, goal=12, brand=-3))
    assert score.scores["goal"] == 10
    assert score.scores["brand"] == 0


def test_code_fence_around_the_json_is_tolerated() -> None:
    """모델이 ```json 으로 감싸는 일이 잦습니다."""
    assert parse_rubric(f"```json\n{rubric_json()}\n```").passed


def test_unparseable_response_is_not_counted_as_a_quality_failure() -> None:
    """파싱 실패는 판정자 문제입니다. 품질 실패로 세면 프롬프트를 고쳐도
    숫자가 안 움직이는 이유를 못 찾습니다."""
    for raw in ("응답 없음", "{", '{"goal": "높음"}', "[]"):
        score = parse_rubric(raw)
        assert score.error, raw
        assert score.mean is None
        assert score.passed is None


def test_missing_item_is_a_scoring_failure() -> None:
    """항목 하나가 빠진 채 평균을 내면 그 항목을 0점으로도 10점으로도 세게 됩니다."""
    partial = json.dumps({"goal": 9, "comment": "사유"}, ensure_ascii=False)
    assert parse_rubric(partial).error


@pytest.mark.asyncio
async def test_situation_and_reason_both_reach_the_judge() -> None:
    """상황 없이 문구만 주면 목적성·공감성을 잴 수 없습니다."""
    judge = FakeChatClient(rubric_json())
    await score_reason(case(), "두부는 있으니 참기름만 더하면 됩니다.", chat=judge)

    assert judge.last_user is not None
    assert "<situation>" in judge.last_user
    assert "<reason>" in judge.last_user


def test_non_finite_scores_are_a_scoring_failure() -> None:
    """`json.loads` 는 NaN/Infinity 를 float 로 받습니다. 숫자 검사를 통과한 뒤
    `round()` 에서 터지면 **판정자 응답 하나 때문에 실험 전체가 중단되고** JSONL 도
    안 남습니다. 25건을 돌린 비용이 통째로 날아갑니다."""
    for literal in ("NaN", "Infinity", "-Infinity"):
        raw = "{" + ", ".join(f'"{key}": {literal}' for key, _, _ in RUBRIC_ITEMS) + ', "comment": "x"}'
        score = parse_rubric(raw)
        assert score.error, literal
        assert score.mean is None
        assert score.passed is None
