"""3단계 산출물을 Neon 에 벌크 적재합니다.

입력은 파이프라인이 남긴 중간 산출물 두 가지뿐입니다.
- `artifacts/records/<dataset>.jsonl` : 2단계 추출 결과
- `artifacts/ingredient_matches.json` : 3단계 매칭 결과

적재는 두 단계입니다.
1. asyncpg COPY 로 UNLOGGED staging 테이블에 밀어넣기 (네트워크 왕복 1회)
2. `sql/` 의 INSERT ... SELECT 로 본 테이블 반영

전체가 한 트랜잭션입니다. 중간에 실패하면 아무것도 남지 않습니다.

주의: SQLAlchemy 의 asyncpg 어댑터는 SQLAlchemy 를 거친 첫 실행 전까지 트랜잭션을 시작하지
않습니다. `engine.begin()` 만 열고 raw 커넥션으로 내려가면 전부 autocommit 이 되므로,
`load_connection_scope()` 가 asyncpg 트랜잭션을 직접 엽니다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from data_pipeline.config import Settings, get_settings
from data_pipeline.db import engine_scope
from data_pipeline.domain import derive_storage_columns, ingredient_match_key, normalize_duration_unit

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
    "image_url",
)
STAGING_RECIPE_STEP_COLUMNS = ("source_type", "source_id", "step_no", "instruction", "image_url")
STAGING_RECIPE_INGREDIENT_COLUMNS = (
    "source_type",
    "source_id",
    "line_no",
    "raw_text",
    "name",
    "normalized_name",
    "quantity",
    "unit",
    "is_required",
    "purpose",
)
STAGING_STORAGE_COLUMNS = (
    "source_item_id",
    "source_food_name",
    "source_food_subtitle",
    "normalized_name",
    "source_slot",
    "storage_location",
    "storage_context",
    "duration_min",
    "duration_max",
    "duration_unit",
    "duration_text",
    "storage_tips",
)
STAGING_MATCH_COLUMNS = ("normalized_name", "ingredient_id", "matched_name", "method", "confidence")

# ON CONFLICT 가 의존하는 유니크 인덱스. 실제 스키마에 이미 있습니다.
REQUIRED_INDEXES = (
    "recipe_source_unique_idx",
    "uq_ingredient_source_identity_key",
    "uq_storage_guideline_source_rule",
)

SQL_STEPS = (
    "001_staging_tables.sql",
    "002_insert_recipe.sql",
    "003_insert_recipe_ingredient.sql",
    "004_insert_storage_guideline.sql",
    "005_insert_recipe_step.sql",
)


@dataclass(slots=True)
class StagingRows:
    """COPY 로 밀어넣을 행 묶음."""

    recipes: list[tuple[Any, ...]] = field(default_factory=list)
    recipe_ingredients: list[tuple[Any, ...]] = field(default_factory=list)
    recipe_steps: list[tuple[Any, ...]] = field(default_factory=list)
    storage: list[tuple[Any, ...]] = field(default_factory=list)
    matches: list[tuple[Any, ...]] = field(default_factory=list)
    skipped_ingredients: int = 0
    skipped_storage: int = 0
    skipped_steps: int = 0
    skipped_recipes: int = 0

    def is_empty(self) -> bool:
        """적재할 것이 하나도 없는지."""
        return not (self.recipes or self.storage)


@dataclass(slots=True)
class LoadReport:
    """적재 결과 요약."""

    staged: dict[str, int] = field(default_factory=dict)
    applied_sql: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    skipped_ingredients: int = 0
    skipped_storage: int = 0
    skipped_steps: int = 0
    skipped_recipes: int = 0

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [f"{name:<28}: {count}행" for name, count in sorted(self.staged.items())]
        if self.skipped_ingredients:
            lines.append(f"{'매칭 실패로 건너뛴 재료줄':<28}: {self.skipped_ingredients}행")
        if self.skipped_storage:
            lines.append(f"{'매칭 실패로 건너뛴 보관기준':<28}: {self.skipped_storage}행")
        if self.skipped_steps:
            lines.append(f"{'내용이 비어 건너뛴 조리단계':<28}: {self.skipped_steps}행")
        if self.skipped_recipes:
            lines.append(f"{'재료 매칭률 미달로 뺀 레시피':<28}: {self.skipped_recipes}건")
        lines.append(f"{'적용한 SQL':<28}: {', '.join(self.applied_sql) or '없음'}")
        lines.extend(f"{table:<28}: {count}행 (적재 후)" for table, count in sorted(self.row_counts.items()))
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


def build_staging_rows(
    records_dir: Path,
    matches: dict[str, int],
    match_meta: dict[str, dict[str, Any]] | None = None,
    *,
    source_type_override: str = "",
    min_match_rate: float = 0.0,
) -> StagingRows:
    """중간 산출물을 COPY 용 튜플로 바꿉니다."""
    rows = StagingRows()
    match_meta = match_meta or {}

    for name, ingredient_id in matches.items():
        meta = match_meta.get(name, {})
        rows.matches.append(
            (
                name,
                ingredient_id,
                str(meta.get("matched_name", ""))[:255],
                str(meta.get("method", "unknown")),
                float(meta.get("confidence", 0.0)),
            )
        )

    for path in sorted(records_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            dataset = payload.get("_dataset", path.stem)
            source_type = source_type_override or dataset

            if "rules" in payload:
                _append_storage(rows, payload, matches)
            elif "ingredients" in payload:
                _append_recipe(rows, payload, matches, source_type=source_type, min_match_rate=min_match_rate)
    return rows


def _match_rate(payload: dict[str, Any], matches: dict[str, int]) -> float:
    """이 레시피의 재료 중 마스터에 붙은 비율. 재료가 없으면 0."""
    keys = [ingredient_match_key(str(item.get("normalized_name") or "")) for item in payload.get("ingredients", [])]
    keys = [key for key in keys if key]
    if not keys:
        return 0.0
    return sum(1 for key in keys if key in matches) / len(keys)


def _is_translated(payload: dict[str, Any]) -> bool:
    """원문이 한국어가 아니라 번역된 레시피인가.

    2단계가 원문이 한국어가 아닐 때만 `name_original` 을 남깁니다.
    한국어 원본 레시피는 MVP 우선 적재 대상이라 매칭률 필터를 걸지 않습니다.
    """
    return bool(str(payload.get("name_original") or "").strip())


def _append_recipe(
    rows: StagingRows,
    payload: dict[str, Any],
    matches: dict[str, int],
    *,
    source_type: str,
    min_match_rate: float = 0.0,
) -> None:
    """ExtractedRecipe 한 건을 staging 행으로."""
    source_id = str(payload.get("source_recipe_id") or payload.get("_entity_key") or "").strip()
    name = _truncate(payload.get("name"), 255)
    if not source_id or not name:
        return

    # 번역 레시피만 매칭률로 거릅니다. 재료가 절반도 안 붙으면 "부족 재료" 계산이
    # 무의미해 데모에 쓸 수 없습니다. 한국어 원본은 그대로 적재합니다.
    if min_match_rate > 0 and _is_translated(payload) and _match_rate(payload, matches) < min_match_rate:
        rows.skipped_recipes += 1
        return

    nutrition = {k: v for k, v in (payload.get("nutrition") or {}).items() if v is not None}
    rows.recipes.append(
        (
            source_type,
            source_id,
            name,
            payload.get("description"),
            _truncate(payload.get("cuisine_type"), 50),
            _truncate(payload.get("difficulty"), 20),
            payload.get("prep_time_min"),
            payload.get("cook_time_min"),
            _decimal(payload.get("servings"), 2),
            _truncate(payload.get("cooking_method"), 50),
            json.dumps(nutrition, ensure_ascii=False),
            [tag.strip() for tag in payload.get("tags", []) if str(tag).strip()],
            _truncate(payload.get("image_url"), 2000),
        )
    )

    _append_recipe_steps(rows, payload, source_type=source_type, source_id=source_id)

    for line_no, item in enumerate(payload.get("ingredients", []), start=1):
        normalized = ingredient_match_key(str(item.get("normalized_name") or ""))
        if not normalized:
            continue
        if normalized not in matches:
            rows.skipped_ingredients += 1
            continue
        rows.recipe_ingredients.append(
            (
                source_type,
                source_id,
                line_no,
                str(item.get("raw_text") or "")[:1000],
                _truncate(item.get("name"), 255) or normalized,
                normalized[:255],
                _decimal(item.get("quantity"), 3),
                _truncate(item.get("unit"), 30),
                bool(item.get("is_required", True)),
                _truncate(item.get("purpose"), 50),
            )
        )


def _append_recipe_steps(
    rows: StagingRows,
    payload: dict[str, Any],
    *,
    source_type: str,
    source_id: str,
) -> None:
    """ExtractedRecipe 의 조리 단계를 staging 행으로.

    `recipe_step` 은 instruction 과 image_url 중 하나는 있어야 한다는 CHECK 를 갖습니다.
    둘 다 빈 단계를 그대로 밀면 적재 전체가 롤백되므로 여기서 걸러 리포트에 셉니다.

    step_no 는 LLM 이 준 번호를 믿지 않고 **살아남은 단계에 1부터 다시 매깁니다.**
    빈 단계를 걸러내면 번호에 구멍이 생기는데, PK 가 (recipe_id, step_no) 라
    구멍 자체는 문제가 없지만 화면이 순서를 그대로 쓰기 때문에 촘촘한 편이 낫습니다.
    """
    step_no = 0
    for item in payload.get("steps") or []:
        instruction = (str(item.get("instruction")).strip() if item.get("instruction") else None) or None
        image_url = _truncate(item.get("image_url"), 2000)
        if instruction is None and image_url is None:
            rows.skipped_steps += 1
            continue
        step_no += 1
        rows.recipe_steps.append((source_type, source_id, step_no, instruction, image_url))


def _append_storage(rows: StagingRows, payload: dict[str, Any], matches: dict[str, int]) -> None:
    """ExtractedStorageItem 한 건을 staging 행으로."""
    normalized = ingredient_match_key(str(payload.get("normalized_name") or ""))
    source_item_id = str(payload.get("source_item_id") or payload.get("_entity_key") or "").strip()
    if not source_item_id:
        return
    if normalized not in matches:
        rows.skipped_storage += len(payload.get("rules", []))
        return

    for rule in payload.get("rules", []):
        slot = str(rule.get("source_slot") or "").strip()
        location, context = derive_storage_columns(slot)
        duration_text = str(rule.get("duration_text") or "").strip()
        if not duration_text:
            continue
        minimum, maximum, unit = _duration_triplet(rule)
        rows.storage.append(
            (
                source_item_id[:255],
                str(payload.get("source_food_name") or "")[:500],
                payload.get("source_food_subtitle"),
                normalized[:255],
                slot,
                location,
                context,
                minimum,
                maximum,
                unit,
                duration_text,
                rule.get("storage_tips"),
            )
        )


def _duration_triplet(rule: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, str | None]:
    """duration 3종을 DB CHECK 에 맞춥니다. 셋 다 있거나 셋 다 없어야 합니다.

    `ck_storage_guideline_duration_complete` 가 부분만 채운 행을 거부합니다.
    실제로 원본 `unit_source` 가 'When Ripe' 처럼 단위가 아닌 문구인 경우가 있어
    LLM 이 수치 없이 단위만 채웠고, 1,298개 중 23개가 여기 걸려 **적재 전체가
    롤백**됐습니다. 한 행 때문에 전부 되돌아가므로 여기서 맞춰 둡니다.

    버리는 것은 단위뿐입니다. 사람이 읽을 표기는 `duration_text` 에 남아 있습니다.

    단위 표기는 `normalize_duration_unit` 으로 한국어 한 벌로 모읍니다.
    """
    minimum = _decimal(rule.get("duration_min"), 2)
    maximum = _decimal(rule.get("duration_max"), 2)
    unit = normalize_duration_unit(_truncate(rule.get("duration_unit"), 50))
    if minimum is None or maximum is None or unit is None:
        return None, None, None
    return minimum, maximum, unit


@asynccontextmanager
async def load_connection_scope(settings: Settings | None = None) -> AsyncIterator[asyncpg.Connection]:
    """적재용 raw asyncpg 커넥션을 트랜잭션 안에서 내어 줍니다."""
    async with engine_scope(direct=True, settings=settings) as engine:
        async with engine.connect() as sa_conn:
            raw = await sa_conn.get_raw_connection()
            conn: asyncpg.Connection = raw.driver_connection  # type: ignore[assignment]
            async with conn.transaction():
                yield conn


async def run_sql_file(conn: asyncpg.Connection, path: Path) -> None:
    """.sql 파일을 통째로 실행합니다.

    asyncpg 는 인자가 없을 때 simple query 프로토콜을 써서 세미콜론으로 구분된
    여러 문장을 한 번에 실행할 수 있습니다.
    """
    await conn.execute(path.read_text(encoding="utf-8"))


async def assert_prerequisites(conn: asyncpg.Connection) -> None:
    """ON CONFLICT 가 기대는 유니크 인덱스가 있는지 확인합니다."""
    found = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1::text[]) "
        "UNION SELECT conname FROM pg_constraint WHERE conname = ANY($1::text[])",
        list(REQUIRED_INDEXES),
    )
    present = {row["indexname"] for row in found}
    missing = [name for name in REQUIRED_INDEXES if name not in present]
    if missing:
        raise RuntimeError(
            "필요한 유니크 제약이 없습니다: " + ", ".join(missing) + ". 다른 Neon 브랜치에 붙었는지 확인하세요."
        )


async def copy_staging(conn: asyncpg.Connection, rows: StagingRows, *, chunk_size: int) -> dict[str, int]:
    """staging 테이블에 COPY 로 적재합니다."""
    plan = (
        ("staging_ingredient_match", STAGING_MATCH_COLUMNS, rows.matches),
        ("staging_recipe", STAGING_RECIPE_COLUMNS, rows.recipes),
        ("staging_recipe_step", STAGING_RECIPE_STEP_COLUMNS, rows.recipe_steps),
        ("staging_recipe_ingredient", STAGING_RECIPE_INGREDIENT_COLUMNS, rows.recipe_ingredients),
        ("staging_storage_guideline", STAGING_STORAGE_COLUMNS, rows.storage),
    )
    staged: dict[str, int] = {}
    for table, columns, records in plan:
        for start in range(0, len(records), chunk_size):
            await conn.copy_records_to_table(table, records=records[start : start + chunk_size], columns=list(columns))
        staged[table] = len(records)
    return staged


async def run_load(
    rows: StagingRows,
    *,
    settings: Settings | None = None,
    truncate_staging_after: bool = False,
) -> LoadReport:
    """staging COPY 후 sql/ 의 문장을 순서대로 적용합니다."""
    settings = settings or get_settings()
    report = LoadReport(
        skipped_ingredients=rows.skipped_ingredients,
        skipped_storage=rows.skipped_storage,
        skipped_steps=rows.skipped_steps,
        skipped_recipes=rows.skipped_recipes,
    )

    if settings.dry_run:
        report.staged = {
            "staging_recipe": len(rows.recipes),
            "staging_recipe_step": len(rows.recipe_steps),
            "staging_recipe_ingredient": len(rows.recipe_ingredients),
            "staging_storage_guideline": len(rows.storage),
            "staging_ingredient_match": len(rows.matches),
        }
        report.applied_sql.append("(DRY_RUN: DB 쓰기 생략)")
        return report

    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / SQL_STEPS[0])
        await assert_prerequisites(conn)
        await conn.execute(
            "TRUNCATE staging_recipe, staging_recipe_ingredient, staging_storage_guideline, staging_ingredient_match"
        )
        report.applied_sql.append(SQL_STEPS[0])

        report.staged = await copy_staging(conn, rows, chunk_size=settings.copy_chunk_size)

        for name in SQL_STEPS[1:]:
            await run_sql_file(conn, settings.sql_dir / name)
            report.applied_sql.append(name)

        for table in ("ingredient", "recipe", "recipe_ingredient", "storage_guideline"):
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            report.row_counts[table] = int(count or 0)

        if truncate_staging_after:
            await run_sql_file(conn, settings.sql_dir / "099_truncate_staging.sql")
            report.applied_sql.append("099_truncate_staging.sql")

    return report


def load_match_metadata(settings: Settings | None = None) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    """3단계 산출물에서 (이름 -> id) 와 부가 정보를 함께 읽습니다."""
    settings = settings or get_settings()
    path = settings.artifacts_dir / "ingredient_matches.json"
    if not path.exists():
        raise FileNotFoundError(f"매칭 결과가 없습니다: {path.name}. 3단계를 먼저 끝내세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    matched = payload["matched"]
    return ({name: int(item["ingredient_id"]) for name, item in matched.items()}, matched)


def collect_rows(settings: Settings | None = None) -> StagingRows:
    """중간 산출물을 읽어 COPY 용 행으로 만듭니다."""
    settings = settings or get_settings()
    matches, meta = load_match_metadata(settings)
    return build_staging_rows(
        settings.artifacts_dir / "records",
        matches,
        meta,
        source_type_override=settings.recipe_source_type,
        min_match_rate=settings.recipe_min_match_rate,
    )
