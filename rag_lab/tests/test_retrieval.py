"""벡터 검색 보조 함수 테스트. DB 없이 돕니다."""

from __future__ import annotations

import pytest

from rag_lab.config import DISTANCE_OPERATORS, Settings
from rag_lab.retrieval import RetrievedDoc, distance_to_score, to_vector_literal


def test_vector_literal_format() -> None:
    """pgvector 가 받는 대괄호 리터럴이어야 합니다."""
    assert to_vector_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"


def test_empty_vector_is_rejected() -> None:
    """빈 벡터로 검색하면 조용히 전체 스캔이 되므로 막습니다."""
    with pytest.raises(ValueError, match="빈 벡터"):
        to_vector_literal([])


@pytest.mark.parametrize(
    ("metric", "distance", "expected"),
    [
        ("cosine", 0.0, 1.0),
        ("cosine", 0.4, 0.6),
        ("l2", 0.0, 1.0),
        ("l2", 1.0, 0.5),
        ("inner_product", -0.8, 0.8),
    ],
)
def test_distance_to_score(metric: str, distance: float, expected: float) -> None:
    """거리 지표별 유사도 변환이 의도대로 되는지 확인합니다."""
    assert distance_to_score(distance, metric) == pytest.approx(expected)


def test_every_metric_has_an_operator() -> None:
    """설정에서 고를 수 있는 지표는 전부 pgvector 연산자가 있어야 합니다."""
    for metric in ("cosine", "l2", "inner_product"):
        assert metric in DISTANCE_OPERATORS


def test_settings_operator_matches_metric() -> None:
    """설정의 distance_metric 이 연산자로 제대로 이어집니다."""
    settings = Settings(_env_file=None, distance_metric="l2")  # type: ignore[call-arg]
    assert settings.distance_operator == "<->"


def test_context_line_includes_source_and_id() -> None:
    """근거 표기에 출처와 id 가 들어가야 인용을 검증할 수 있습니다."""
    doc = RetrievedDoc(source="recipe", doc_id=12, title="김치찌개", body="얼큰한 찌개", distance=0.1, score=0.9)
    assert doc.as_context() == "[recipe#12] 김치찌개 - 얼큰한 찌개"
