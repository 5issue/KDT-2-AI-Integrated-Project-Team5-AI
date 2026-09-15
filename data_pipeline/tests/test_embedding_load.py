"""embedding 적재 검증. DB 도 API 도 쓰지 않습니다.

여기서 걸리는 실수가 실제로 돈과 시간을 씁니다. 4,667행을 다 부르고 나서 차원이 안 맞는
것을 알면 처음부터 다시 돌려야 합니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data_pipeline.load import embedding

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeEmbeddings:
    """호출을 기록하는 가짜 임베딩 엔드포인트."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.batches: list[list[str]] = []

    def create(self, *, model: str, input: list[str], dimensions: int):  # noqa: A002
        """입력 순서를 일부러 뒤집어 돌려줍니다. 호출부가 index 로 되돌리는지 봅니다."""
        self.batches.append(list(input))
        items = [
            type("Item", (), {"index": index, "embedding": [float(index)] * self.dim})() for index in range(len(input))
        ]
        return type("Response", (), {"data": list(reversed(items))})()


class FakeClient:
    """`OpenAI` 자리에 끼우는 최소 구현."""

    def __init__(self, dim: int = 4) -> None:
        self.embeddings = FakeEmbeddings(dim)


def test_already_filled_rows_are_skipped() -> None:
    """중간에 끊겼을 때 처음부터 다시 돌리면 돈이 두 배로 듭니다."""
    for target in embedding.TARGETS:
        assert "embedding IS NULL" in embedding.select_sql(target), target


def test_natural_keys_match_the_apply_sql() -> None:
    """staging 의 자연키가 `020_update_embedding.sql` 과 달라지면 한 행도 반영되지 않습니다.

    staging 에는 멀쩡히 쌓이고 본 테이블만 비어 있어서, 원인을 찾기가 특히 어렵습니다.
    """
    apply_sql = (REPO_ROOT / "data_pipeline" / "sql" / "020_update_embedding.sql").read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", apply_sql)

    expected = {
        "recipe": "r.source_type || ':' || r.source_recipe_id",
        "product": "p.source_type || ':' || p.source_product_id",
        "ingredient": "i.source_identity_key",
    }
    for target, key_sql in expected.items():
        assert embedding.TEXT_SQL[target][0] == key_sql, target
        # 별칭만 다를 뿐 같은 표현이 020 에도 있어야 합니다.
        column = key_sql.split(" || ")[-1].split(".")[-1]
        assert column in normalized, f"{target}: {column} 이 020 에 없습니다"


def test_search_body_and_embedding_text_cover_the_same_sources() -> None:
    """임베딩 대상과 검색 대상이 어긋나면 "검색은 됐는데 근거가 빈" 상태가 됩니다.

    `rag_lab` 을 import 하지 않습니다. data_pipeline 이 rag_lab 에 의존하면 안 됩니다.
    파일을 텍스트로 읽어 대상 이름만 맞춰 봅니다.
    """
    retrieval = (REPO_ROOT / "rag_lab" / "src" / "rag_lab" / "retrieval.py").read_text(encoding="utf-8")
    block = retrieval[retrieval.index("_SOURCES: dict") : retrieval.index("@dataclass")]
    sources = set(re.findall(r'^\s{4}"(\w+)":', block, re.MULTILINE))

    assert sources == set(embedding.TARGETS)


def test_dimension_mismatch_fails_before_the_database() -> None:
    """embedding 컬럼이 VECTOR(1536) 입니다. 차원이 다르면 INSERT 에서 터지는데 원인이 멉니다."""
    client = FakeClient(dim=1024)

    with pytest.raises(RuntimeError, match="임베딩 차원이 1024"):
        embedding.embed_texts(client, ["가"], model="bge-m3", dimensions=1536)  # type: ignore[arg-type]


def test_batches_are_chunked_and_order_is_restored() -> None:
    """한 줄씩 보내면 4,667번 왕복합니다. 묶어 보내되 순서는 지켜야 합니다."""
    client = FakeClient(dim=2)
    texts = [f"문장{index}" for index in range(embedding.BATCH_SIZE + 5)]

    vectors = embedding.embed_texts(client, texts, model="m", dimensions=2)  # type: ignore[arg-type]

    assert [len(batch) for batch in client.embeddings.batches] == [embedding.BATCH_SIZE, 5]
    # 가짜 응답이 순서를 뒤집어 주는데, index 로 되돌려야 입력 순서와 맞습니다.
    assert vectors[0] == [0.0, 0.0]
    assert vectors[embedding.BATCH_SIZE] == [0.0, 0.0]
    assert vectors[1] == [1.0, 1.0]


def test_vector_literal_is_pgvector_shaped() -> None:
    """asyncpg 에 vector 코덱을 등록하지 않으므로 리터럴 문자열로 넘깁니다."""
    assert embedding.to_vector_literal([1.0, -0.5, 0.25]) == "[1,-0.5,0.25]"


def test_cost_estimate_has_the_right_order_of_magnitude() -> None:
    """비용을 미리 보여 주기 위한 값입니다. 정확할 필요는 없고 자릿수만 맞으면 됩니다."""
    assert embedding.estimate_tokens(["가" * 100]) == 50
    assert embedding.estimate_tokens([]) == 0


def test_report_says_whether_it_actually_applied() -> None:
    """dry-run 을 실제 적재로 착각하면 임베딩이 비어 있는 채로 실험을 시작하게 됩니다."""
    report = embedding.EmbeddingReport(embedded={"recipe": 10}, estimated_tokens=1000)

    assert "안 함(dry-run)" in report.render()
    report.applied = True
    assert "완료" in report.render()


def test_refresh_rebuilds_rows_that_already_have_embeddings() -> None:
    """임베딩 텍스트가 바뀌면 기존 임베딩이 낡습니다.

    레시피 임베딩에는 재료명이 들어갑니다. 재매칭으로 재료 연결이 늘면(56% -> 79%)
    옛 임베딩은 그때의 재료 기준이라, 검색이 과거 상태로 돕니다.
    """
    assert "embedding IS NULL" in embedding.select_sql("recipe")
    assert "embedding IS NULL" not in embedding.select_sql("recipe", refresh=True)


def test_default_still_skips_filled_rows() -> None:
    """기본값까지 다시 만들면 중간에 끊겼을 때 돈이 두 배로 듭니다."""
    for target in embedding.TARGETS:
        assert "embedding IS NULL" in embedding.select_sql(target), target
