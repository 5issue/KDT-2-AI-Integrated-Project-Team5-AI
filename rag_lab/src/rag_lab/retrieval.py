"""pgvector 기반 검색.

벡터는 문자열 리터럴로 만들어 `CAST(:vec AS vector)` 로 넘깁니다. asyncpg 에 vector 코덱을
등록하지 않아도 되고, 값이 SQL 문자열에 끼어들지 않아 인젝션 경로도 생기지 않습니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_lab.config import DISTANCE_OPERATORS, Settings, get_settings

SourceTable = Literal["recipe", "product", "ingredient"]

# 검색 대상별로 (테이블, 식별자 컬럼, 제목 컬럼, 본문으로 쓸 표현식)
_SOURCES: dict[str, tuple[str, str, str, str]] = {
    "recipe": ("recipe", "recipe_id", "name", "COALESCE(description, '')"),
    "product": ("product", "product_id", "name", "COALESCE(metadata->>'summary', '')"),
    "ingredient": ("ingredient", "ingredient_id", "name", "normalized_name"),
}


@dataclass(slots=True, frozen=True)
class RetrievedDoc:
    """검색으로 건진 근거 하나."""

    source: str
    doc_id: int
    title: str
    body: str
    distance: float
    score: float

    def as_context(self) -> str:
        """프롬프트에 넣을 형태."""
        body = f" - {self.body}" if self.body else ""
        return f"[{self.source}#{self.doc_id}] {self.title}{body}"


def to_vector_literal(vector: Sequence[float]) -> str:
    """파이썬 실수 목록을 pgvector 리터럴 문자열로 바꿉니다."""
    if not vector:
        raise ValueError("빈 벡터는 검색에 쓸 수 없습니다.")
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def distance_to_score(distance: float, metric: str) -> float:
    """거리 지표를 0~1 에 가까운 유사도로 바꿉니다.

    cosine 거리는 0~2 범위라 `1 - distance` 가 곧 코사인 유사도입니다.
    l2 는 상한이 없어 `1 / (1 + distance)` 로 눌러 씁니다.
    inner_product 는 pgvector 가 음수 내적을 주므로 부호만 뒤집습니다.
    """
    if metric == "cosine":
        return 1.0 - distance
    if metric == "l2":
        return 1.0 / (1.0 + distance)
    return -distance


async def search(
    conn: AsyncConnection,
    embedding: Sequence[float],
    *,
    source: SourceTable = "recipe",
    top_k: int | None = None,
    threshold: float | None = None,
    settings: Settings | None = None,
) -> list[RetrievedDoc]:
    """임베딩과 가까운 행을 가져옵니다. embedding 이 비어 있는 행은 건너뜁니다."""
    settings = settings or get_settings()
    if source not in _SOURCES:
        raise ValueError(f"지원하지 않는 검색 대상입니다: {source!r} ({sorted(_SOURCES)})")

    table, id_column, title_column, body_expr = _SOURCES[source]
    operator = DISTANCE_OPERATORS[settings.distance_metric]
    top_k = top_k or settings.top_k
    threshold = settings.score_threshold if threshold is None else threshold

    # 연산자와 컬럼명은 화이트리스트에서만 나오고, 값은 전부 바인딩으로 넘어갑니다.
    statement = text(
        f"SELECT {id_column} AS doc_id, "
        f"       {title_column} AS title, "
        f"       {body_expr} AS body, "
        f"       (embedding {operator} CAST(:query_vector AS vector)) AS distance "
        f"FROM {table} "
        f"WHERE embedding IS NOT NULL "
        f"ORDER BY embedding {operator} CAST(:query_vector AS vector) "
        f"LIMIT :top_k"
    )
    rows = (await conn.execute(statement, {"query_vector": to_vector_literal(embedding), "top_k": top_k})).mappings()

    docs: list[RetrievedDoc] = []
    for row in rows:
        distance = float(row["distance"])
        score = distance_to_score(distance, settings.distance_metric)
        if score < threshold:
            continue
        docs.append(
            RetrievedDoc(
                source=source,
                doc_id=int(row["doc_id"]),
                title=str(row["title"]),
                body=str(row["body"] or ""),
                distance=distance,
                score=score,
            )
        )
    return docs
