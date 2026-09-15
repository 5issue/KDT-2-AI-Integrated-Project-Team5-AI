"""recipe / product / ingredient 의 embedding 컬럼 채우기.

pgvector 검색(`rag_lab`)이 여기에 의존합니다. 임베딩이 비어 있으면 검색이 아무것도 못
찾고, `rag_lab` 은 근거가 없으면 LLM 을 아예 부르지 않으므로 RAG 전체가 멈춥니다.

## 임베딩 텍스트는 검색 본문과 같아야 합니다

다르면 "검색은 됐는데 근거가 비어 있는" 상태가 됩니다. 아래 `TEXT_SQL` 의 표현식은
`rag_lab/src/rag_lab/retrieval.py` 의 `_SOURCES` 본문과 짝입니다. **한쪽만 고치지 마세요.**

## 왜 이름만으로는 부족한가

사용자 질문은 대개 재료로 들어옵니다("김치랑 돼지고기 있는데 뭐 해먹지"). 레시피 이름과
설명만 임베딩하면 그 질문이 안 걸립니다. 그래서 레시피에 재료명을 함께 넣습니다.
`recipe.description` 이 54%만 채워져 있는 것도 이걸로 메워집니다.

## 비용

4,667행 × 대략 100토큰 = 약 47만 토큰. `text-embedding-3-small` 이 100만 토큰당 $0.02 이라
한 바퀴에 약 $0.01 입니다. Batch API(50% 할인)를 쓸 이유가 없어 동기로 돌립니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import asyncpg
from openai import OpenAI

from data_pipeline.config import Settings, get_settings
from data_pipeline.load.bulk_insert import load_connection_scope, run_sql_file

# 임베딩 대상별 (자연키 표현식, 임베딩할 텍스트 표현식).
#
# 자연키는 `sql/020_update_embedding.sql` 이 본 테이블과 맞추는 값과 **같아야** 합니다.
# 다르면 staging 에는 쌓이는데 본 테이블에는 한 행도 반영되지 않습니다.
TEXT_SQL: dict[str, tuple[str, str]] = {
    "recipe": (
        "r.source_type || ':' || r.source_recipe_id",
        """
        CONCAT_WS(' ',
            r.name,
            NULLIF(r.description, ''),
            (
                SELECT STRING_AGG(i.name, ' ' ORDER BY ri.is_required DESC, i.name)
                FROM recipe_ingredient ri
                JOIN ingredient i ON i.ingredient_id = ri.ingredient_id
                WHERE ri.recipe_id = r.recipe_id
            ),
            ARRAY_TO_STRING(r.tags, ' '),
            r.cooking_method
        )
        """,
    ),
    "product": (
        "p.source_type || ':' || p.source_product_id",
        """
        CONCAT_WS(' ',
            p.name,
            (SELECT c.name FROM category c WHERE c.category_id = p.category_id),
            NULLIF(p.origin_country, ''),
            (
                SELECT STRING_AGG(i.name, ' ' ORDER BY i.name)
                FROM product_ingredient pi
                JOIN ingredient i ON i.ingredient_id = pi.ingredient_id
                WHERE pi.product_id = p.product_id
            )
        )
        """,
    ),
    "ingredient": (
        "i.source_identity_key",
        "CONCAT_WS(' ', i.name, NULLIF(i.normalized_name, i.name), ARRAY_TO_STRING(i.aliases, ' '))",
    ),
}

_FROM: dict[str, str] = {
    "recipe": "recipe r",
    "product": "product p",
    "ingredient": "ingredient i",
}
_ALIAS: dict[str, str] = {"recipe": "r", "product": "p", "ingredient": "i"}

TARGETS = tuple(TEXT_SQL)

# 한 요청에 묶어 보낼 행 수. 한 줄씩 보내면 4,667번 왕복합니다.
BATCH_SIZE = 128


@dataclass(slots=True)
class EmbeddingReport:
    """무엇을 얼마나 채웠는지."""

    embedded: dict[str, int] = field(default_factory=dict)
    skipped_empty: dict[str, int] = field(default_factory=dict)
    applied: bool = False
    estimated_tokens: int = 0

    @property
    def total(self) -> int:
        """전체 임베딩 행 수."""
        return sum(self.embedded.values())

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [f"{name}: {count}행" for name, count in sorted(self.embedded.items())]
        empty = [f"{name}: {count}행" for name, count in sorted(self.skipped_empty.items()) if count]
        if empty:
            lines.append("텍스트가 비어 건너뜀 — " + ", ".join(empty))
        lines.append(f"추정 토큰 {self.estimated_tokens:,} (약 ${self.estimated_tokens / 1_000_000 * 0.02:.3f})")
        lines.append("본 테이블 반영: " + ("완료" if self.applied else "안 함(dry-run)"))
        return "\n".join(lines)


def select_sql(target: str) -> str:
    """임베딩 대상 행과 텍스트를 뽑는 SELECT.

    `embedding IS NULL` 로 이미 채워진 행을 건너뜁니다. 중간에 끊겼을 때 처음부터 다시
    돌리면 돈과 시간이 두 배로 듭니다.
    """
    key_sql, text_sql = TEXT_SQL[target]
    alias = _ALIAS[target]
    return f"""
        SELECT {key_sql} AS target_key, {text_sql} AS body
        FROM {_FROM[target]}
        WHERE {alias}.embedding IS NULL
        ORDER BY 1
    """


async def collect_targets(conn: asyncpg.Connection, target: str) -> list[tuple[str, str]]:
    """(자연키, 임베딩할 텍스트) 목록. 텍스트가 빈 행은 빼고 돌려줍니다."""
    rows = await conn.fetch(select_sql(target))
    return [(row["target_key"], body) for row in rows if (body := (row["body"] or "").strip())]


def estimate_tokens(texts: Sequence[str]) -> int:
    """대략적인 토큰 수. 비용을 미리 보여 주기 위한 값입니다.

    한국어는 문자당 토큰 비율이 영어보다 높아 보수적으로 문자 수의 절반으로 봅니다.
    정확할 필요는 없고 자릿수만 맞으면 됩니다.
    """
    return sum(len(text) for text in texts) // 2


def embed_texts(client: OpenAI, texts: Sequence[str], *, model: str, dimensions: int) -> list[list[float]]:
    """텍스트를 벡터로. 입력 순서를 그대로 지킵니다."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        chunk = list(texts[start : start + BATCH_SIZE])
        response = client.embeddings.create(model=model, input=chunk, dimensions=dimensions)
        ordered = sorted(response.data, key=lambda item: item.index)
        for item in ordered:
            vector = list(item.embedding)
            if len(vector) != dimensions:
                # embedding 컬럼이 VECTOR(1536) 입니다. 차원이 다르면 COPY 에서 터지는데,
                # 그때는 원인이 멀리 있습니다. 여기서 바로 알립니다.
                raise RuntimeError(
                    f"임베딩 차원이 {len(vector)} 인데 EMBEDDING_DIM 은 {dimensions} 입니다. "
                    f"모델({model})을 바꿨다면 embedding 컬럼 마이그레이션이 함께 필요합니다."
                )
            vectors.append(vector)
    return vectors


def to_vector_literal(vector: Sequence[float]) -> str:
    """pgvector 리터럴 문자열. asyncpg 에 vector 코덱을 등록하지 않아도 됩니다."""
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


async def run_embedding(
    targets: Sequence[str],
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    client: OpenAI | None = None,
) -> EmbeddingReport:
    """대상 행을 임베딩해 staging 에 넣고 본 테이블에 반영합니다.

    `dry_run` 이면 대상 수와 추정 비용만 세고 API 를 부르지 않습니다.
    """
    settings = settings or get_settings()
    report = EmbeddingReport()

    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / "001_staging_tables.sql")

        for target in targets:
            total_rows = await conn.fetchval(
                f"SELECT COUNT(*) FROM {_FROM[target]} WHERE {_ALIAS[target]}.embedding IS NULL"
            )
            pairs = await collect_targets(conn, target)
            report.skipped_empty[target] = int(total_rows or 0) - len(pairs)
            report.estimated_tokens += estimate_tokens([text for _, text in pairs])

            if dry_run or not pairs:
                report.embedded[target] = len(pairs)
                continue

            client = client or OpenAI(api_key=settings.require_openai_api_key())
            vectors = embed_texts(
                client,
                [text for _, text in pairs],
                model=settings.openai_embedding_model,
                dimensions=settings.embedding_dim,
            )
            # COPY 를 쓰지 않습니다. asyncpg 의 COPY 는 바이너리 형식이라 `vector` 처럼
            # 확장이 추가한 타입은 인코더가 없어 `no binary format encoder for type vector`
            # 로 막힙니다. 코덱을 등록하는 방법도 있지만, 4,667행이면 executemany 로 충분하고
            # 텍스트 리터럴에 `::vector` 캐스팅을 붙이는 쪽이 읽기 쉽습니다.
            await conn.executemany(
                "INSERT INTO staging_embedding (target_table, target_key, embedding) "
                "VALUES ($1, $2, $3::vector) "
                "ON CONFLICT (target_table, target_key) DO UPDATE SET embedding = EXCLUDED.embedding",
                [(target, key, to_vector_literal(vector)) for (key, _), vector in zip(pairs, vectors, strict=True)],
            )
            report.embedded[target] = len(pairs)

        if not dry_run and report.total:
            await run_sql_file(conn, settings.sql_dir / "020_update_embedding.sql")
            report.applied = True

    return report
