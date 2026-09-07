"""검증된 파싱 결과를 Neon 에 벌크 적재합니다.

전략은 두 단계입니다.

1. asyncpg 의 COPY(`copy_records_to_table`)로 UNLOGGED staging 테이블에 원본 그대로 밀어넣기.
   행 단위 INSERT 보다 훨씬 빠르고, 네트워크 왕복이 한 번입니다.
2. `sql/` 의 INSERT ... SELECT 문으로 staging -> 본 테이블 반영.
   비즈니스 규칙(매칭, 중복 제거, upsert)은 전부 SQL 한 곳에 모아 두어
   pytest 로 따로 검증할 수 있게 했습니다.

재료는 **매칭만** 합니다. `ingredient` 는 K-FIND 코드 체계로 큐레이션된 마스터라
파이프라인이 새 행을 만들지 않습니다. 마스터에 없는 재료는 적재 리포트로 보고되고,
사람이 검토한 뒤 마스터에 추가하는 흐름입니다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from data_pipeline.batch.results import ParsedRecipeRecord
from data_pipeline.config import Settings, get_settings
from data_pipeline.db import engine_scope

STAGING_RECIPE_COLUMNS = (
    "source_type",
    "source_id",
    "name",
    "description",
    "cuisine_type",
    "difficulty",
    "prep_time_min",
    "cook_time_min",
    "servings",
    "cooking_method",
    "nutrition",
    "tags",
)
STAGING_RECIPE_INGREDIENT_COLUMNS = (
    "source_id",
    "line_no",
    "raw_text",
    "name",
    "normalized_name",
    "quantity",
    "unit",
    "is_required",
    "role",
    "purpose",
)

# 003/004 의 ON CONFLICT 가 의존하는 유니크 인덱스. 실제 스키마에 이미 있습니다.
# 없으면 다른 Neon 브랜치에 붙었다는 뜻이라 적재 전에 막습니다.
REQUIRED_INDEXES = ("recipe_source_unique_idx", "uq_ingredient_source_identity_key")

SQL_STEPS = (
    "001_staging_tables.sql",
    "002_match_ingredient.sql",
    "003_insert_recipe.sql",
    "004_insert_recipe_ingredient.sql",
)


@dataclass(slots=True)
class UnmatchedIngredient:
    """마스터에서 못 찾은 재료. 사람이 검토할 대상입니다."""

    normalized_name: str
    sample_raw_text: str
    occurrence_count: int
    recipe_count: int


@dataclass(slots=True)
class LoadReport:
    """적재 결과 요약."""

    staged_recipes: int = 0
    staged_ingredient_lines: int = 0
    matched_ingredients: int = 0
    unmatched: list[UnmatchedIngredient] = field(default_factory=list)
    applied_sql: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    @property
    def match_rate(self) -> float | None:
        """매칭된 재료 종류의 비율."""
        total = self.matched_ingredients + len(self.unmatched)
        return self.matched_ingredients / total if total else None

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [
            f"staging_recipe            : {self.staged_recipes}행",
            f"staging_recipe_ingredient : {self.staged_ingredient_lines}행",
            f"재료 매칭                 : {self.matched_ingredients}종 매칭 / {len(self.unmatched)}종 미매칭",
        ]
        if (rate := self.match_rate) is not None:
            lines.append(f"매칭률                    : {rate:.1%}")
        lines.append(f"적용한 SQL                : {', '.join(self.applied_sql) or '없음'}")
        lines.extend(f"{table:<26}: {count}행" for table, count in sorted(self.row_counts.items()))

        if self.unmatched:
            lines.append("\n마스터에 없어 건너뛴 재료 (검토 필요):")
            for item in self.unmatched[:30]:
                lines.append(
                    f"  {item.normalized_name:<16} {item.occurrence_count:>4}회 "
                    f"/ 레시피 {item.recipe_count:>3}건   예: {item.sample_raw_text[:40]}"
                )
            if len(self.unmatched) > 30:
                lines.append(f"  ... 외 {len(self.unmatched) - 30}종")
        return "\n".join(lines)


def _decimal(value: float | int | None, places: int) -> Decimal | None:
    """asyncpg 의 NUMERIC 코덱은 Decimal 을 요구하므로 변환합니다."""
    if value is None:
        return None
    return Decimal(str(round(float(value), places)))


def _truncate(value: str | None, limit: int) -> str | None:
    """VARCHAR 길이 제한에 맞춰 자릅니다. LLM 출력이 길 수 있어 방어적으로 둡니다."""
    if value is None:
        return None
    value = value.strip()
    return value[:limit] if value else None


def to_staging_rows(
    records: Sequence[ParsedRecipeRecord],
    *,
    source_type: str,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """ParsedRecipeRecord 를 COPY 용 튜플 두 벌로 변환합니다."""
    recipe_rows: list[tuple[Any, ...]] = []
    ingredient_rows: list[tuple[Any, ...]] = []

    for record in records:
        recipe = record.recipe
        nutrition = recipe.nutrition.model_dump(exclude_none=True) if recipe.nutrition else {}
        recipe_rows.append(
            (
                source_type,
                record.source_id,
                _truncate(recipe.name, 255) or record.source_id,
                recipe.description,
                _truncate(recipe.cuisine_type, 50),
                _truncate(recipe.difficulty, 20),
                recipe.prep_time_min,
                recipe.cook_time_min,
                _decimal(recipe.servings, 2),
                _truncate(recipe.cooking_method, 50),
                json.dumps(nutrition, ensure_ascii=False),
                [tag.strip() for tag in recipe.tags if tag.strip()],
            )
        )
        for line_no, item in enumerate(recipe.ingredients, start=1):
            normalized = item.normalized_name.strip().lower()
            if not normalized:
                continue
            ingredient_rows.append(
                (
                    record.source_id,
                    line_no,
                    item.raw_text,
                    _truncate(item.name, 255) or normalized,
                    normalized[:255],
                    _decimal(item.quantity, 3),
                    _truncate(item.unit, 30),
                    item.is_required,
                    item.role,
                    _truncate(item.purpose, 50),
                )
            )
    return recipe_rows, ingredient_rows


async def run_sql_file(conn: asyncpg.Connection, path: Path) -> None:
    """.sql 파일을 통째로 실행합니다.

    asyncpg 는 인자가 없을 때 simple query 프로토콜을 써서 세미콜론으로 구분된
    여러 문장을 한 번에 실행할 수 있습니다. SQLAlchemy 의 exec_driver_sql 은
    prepared statement 를 쓰기 때문에 여러 문장을 받지 못합니다.
    """
    await conn.execute(path.read_text(encoding="utf-8"))


async def assert_prerequisites(conn: asyncpg.Connection) -> None:
    """ON CONFLICT 가 기대는 유니크 인덱스가 있는지 확인합니다."""
    rows = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1::text[])",
        list(REQUIRED_INDEXES),
    )
    present = {row["indexname"] for row in rows}
    missing = [name for name in REQUIRED_INDEXES if name not in present]
    if missing:
        raise RuntimeError(
            "필요한 유니크 인덱스가 없습니다: "
            + ", ".join(missing)
            + ". 스키마가 다른 Neon 브랜치에 붙었는지 확인하세요."
        )


async def copy_staging(
    conn: asyncpg.Connection,
    recipe_rows: Sequence[tuple[Any, ...]],
    ingredient_rows: Sequence[tuple[Any, ...]],
    *,
    chunk_size: int,
) -> None:
    """staging 테이블에 COPY 로 적재합니다."""
    for start in range(0, len(recipe_rows), chunk_size):
        await conn.copy_records_to_table(
            "staging_recipe",
            records=recipe_rows[start : start + chunk_size],
            columns=list(STAGING_RECIPE_COLUMNS),
        )
    for start in range(0, len(ingredient_rows), chunk_size):
        await conn.copy_records_to_table(
            "staging_recipe_ingredient",
            records=ingredient_rows[start : start + chunk_size],
            columns=list(STAGING_RECIPE_INGREDIENT_COLUMNS),
        )


async def collect_match_report(conn: asyncpg.Connection, report: LoadReport) -> None:
    """재료 매칭 결과를 리포트에 채웁니다."""
    report.matched_ingredients = int(await conn.fetchval("SELECT COUNT(*) FROM staging_ingredient_match") or 0)
    rows = await conn.fetch(
        "SELECT normalized_name, sample_raw_text, occurrence_count, recipe_count "
        "FROM staging_unmatched_ingredient ORDER BY occurrence_count DESC, normalized_name"
    )
    report.unmatched = [
        UnmatchedIngredient(
            normalized_name=str(row["normalized_name"]),
            sample_raw_text=str(row["sample_raw_text"]),
            occurrence_count=int(row["occurrence_count"]),
            recipe_count=int(row["recipe_count"]),
        )
        for row in rows
    ]


@asynccontextmanager
async def load_connection_scope(settings: Settings | None = None) -> AsyncIterator[asyncpg.Connection]:
    """적재용 raw asyncpg 커넥션을 트랜잭션 안에서 내어 줍니다.

    트랜잭션을 asyncpg 쪽에서 직접 여는 이유가 있습니다. SQLAlchemy 의 asyncpg 어댑터는
    SQLAlchemy 를 거친 첫 실행 시점에야 트랜잭션을 시작합니다. `engine.begin()` 을 열어 두고
    곧바로 `driver_connection` 으로 내려가서 실행하면 트랜잭션이 시작된 적이 없어 전부
    autocommit 으로 돕니다. 그러면 004 에서 실패해도 003 이 넣은 레시피가 남습니다.

    벌크 적재는 pooler 가 아니라 direct 엔드포인트로 붙습니다(COPY, 긴 트랜잭션).
    """
    async with engine_scope(direct=True, settings=settings) as engine:
        async with engine.connect() as sa_conn:
            raw = await sa_conn.get_raw_connection()
            conn: asyncpg.Connection = raw.driver_connection  # type: ignore[assignment]
            async with conn.transaction():
                yield conn


async def run_load(
    records: Sequence[ParsedRecipeRecord],
    *,
    settings: Settings | None = None,
    truncate_staging_after: bool = False,
) -> LoadReport:
    """파싱 결과를 staging 에 COPY 하고 sql/ 의 문장을 순서대로 적용합니다.

    전체가 한 트랜잭션입니다. 중간에 실패하면 아무것도 남지 않습니다.
    """
    settings = settings or get_settings()
    recipe_rows, ingredient_rows = to_staging_rows(records, source_type=settings.recipe_source_type)
    report = LoadReport(staged_recipes=len(recipe_rows), staged_ingredient_lines=len(ingredient_rows))

    if settings.dry_run:
        report.applied_sql.append("(DRY_RUN: DB 쓰기 생략)")
        return report

    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / SQL_STEPS[0])
        await assert_prerequisites(conn)
        await conn.execute("TRUNCATE staging_recipe, staging_recipe_ingredient")
        report.applied_sql.append(SQL_STEPS[0])

        await copy_staging(conn, recipe_rows, ingredient_rows, chunk_size=settings.copy_chunk_size)

        for name in SQL_STEPS[1:]:
            await run_sql_file(conn, settings.sql_dir / name)
            report.applied_sql.append(name)

        await collect_match_report(conn, report)

        for table in ("ingredient", "recipe", "recipe_ingredient"):
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            report.row_counts[table] = int(count or 0)

        if truncate_staging_after:
            await run_sql_file(conn, settings.sql_dir / "099_truncate_staging.sql")
            report.applied_sql.append("099_truncate_staging.sql")

    return report
