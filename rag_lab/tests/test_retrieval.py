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


# --- 임베딩 텍스트 <-> 검색 본문 짝 (코드래빗 리뷰 #14) -----------------------
#
# **방향이 있는 불변식입니다.** 임베딩에 든 필드는 본문에도 있어야 합니다.
# 임베딩에만 있으면 "검색은 됐는데 근거에는 그 이유가 없는" 상태가 됩니다.
# 본문에만 있는 것(`storage_type`)은 추가 맥락이라 괜찮습니다.

# (대상, 임베딩과 본문 양쪽에 있어야 하는 표식)
#
# `name` 은 제외합니다. `_SOURCES` 에서 본문이 아니라 title_column 으로 따로 나갑니다.
_PAIRED_FIELDS = {
    "recipe": ("description", "recipe_ingredient", "tags", "cooking_method"),
    "product": ("category", "origin_country", "product_ingredient"),
    "ingredient": ("normalized_name", "aliases"),
}


@pytest.mark.parametrize("target", sorted(_PAIRED_FIELDS))
def test_embedding_fields_are_visible_in_the_retrieval_body(target: str) -> None:
    """임베딩에 쓴 필드가 본문에서 빠지면 검색된 이유를 사용자가 볼 수 없습니다.

    실제로 어긋나 있었습니다 - product 임베딩은 재료명을 넣는데 본문에는 없었고,
    recipe 의 `tags` 도 본문에서 빠져 있었습니다.
    """
    from data_pipeline.load.embedding import TEXT_SQL
    from rag_lab.retrieval import _SOURCES

    _, embedding_text = TEXT_SQL[target]
    body = _SOURCES[target][3]

    for marker in _PAIRED_FIELDS[target]:
        assert marker in embedding_text, f"{target} 임베딩 텍스트에 {marker} 가 없습니다"
        assert marker in body, f"{target} 검색 본문에 {marker} 가 빠졌습니다 (임베딩에는 있습니다)"
