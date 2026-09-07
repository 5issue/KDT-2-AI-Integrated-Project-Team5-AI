"""COPY 용 행 변환 테스트. DB 없이 순수 변환만 확인합니다."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from data_pipeline.batch.results import parse_recipe_results
from data_pipeline.load.bulk_insert import (
    STAGING_RECIPE_COLUMNS,
    STAGING_RECIPE_INGREDIENT_COLUMNS,
    LoadReport,
    UnmatchedIngredient,
    to_staging_rows,
)

SOURCE_TYPE = "TEST_PARSE"


def test_staging_rows_match_column_order(samples_dir: Path) -> None:
    """튜플 길이가 COPY 컬럼 수와 맞아야 asyncpg 가 받아줍니다."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")
    recipe_rows, ingredient_rows = to_staging_rows(outcome.records, source_type=SOURCE_TYPE)

    assert len(recipe_rows) == 2
    assert all(len(row) == len(STAGING_RECIPE_COLUMNS) for row in recipe_rows)
    assert all(len(row) == len(STAGING_RECIPE_INGREDIENT_COLUMNS) for row in ingredient_rows)


def test_recipe_natural_key_is_source_type_and_id(samples_dir: Path) -> None:
    """레시피 자연키는 (source_type, source_recipe_id) 입니다. 이름이 아닙니다."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")
    recipe_rows, _ = to_staging_rows(outcome.records, source_type=SOURCE_TYPE)

    assert recipe_rows[0][STAGING_RECIPE_COLUMNS.index("source_type")] == SOURCE_TYPE
    assert recipe_rows[0][STAGING_RECIPE_COLUMNS.index("source_id")] == "sample-001"


def test_numeric_columns_are_decimal(samples_dir: Path) -> None:
    """asyncpg 의 NUMERIC 코덱은 float 을 거부하므로 Decimal 이어야 합니다."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")
    recipe_rows, ingredient_rows = to_staging_rows(outcome.records, source_type=SOURCE_TYPE)

    servings = recipe_rows[0][STAGING_RECIPE_COLUMNS.index("servings")]
    assert isinstance(servings, Decimal)

    quantity = ingredient_rows[0][STAGING_RECIPE_INGREDIENT_COLUMNS.index("quantity")]
    assert isinstance(quantity, Decimal)


def test_jsonb_and_array_columns(samples_dir: Path) -> None:
    """nutrition 은 JSON 문자열, tags 는 list[str] 로 넘겨야 합니다(실제 컬럼은 text[])."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")
    recipe_rows, _ = to_staging_rows(outcome.records, source_type=SOURCE_TYPE)

    nutrition = recipe_rows[0][STAGING_RECIPE_COLUMNS.index("nutrition")]
    tags = recipe_rows[0][STAGING_RECIPE_COLUMNS.index("tags")]
    assert isinstance(nutrition, str) and nutrition.startswith("{")
    assert tags == ["한식", "국물", "김치"]

    # nutrition 이 null 인 레시피는 빈 JSON 객체로 채웁니다(NOT NULL 컬럼).
    assert recipe_rows[1][STAGING_RECIPE_COLUMNS.index("nutrition")] == "{}"


def test_normalized_name_is_lowercased_and_trimmed(samples_dir: Path) -> None:
    """매칭 조인 키가 되므로 정규화 규칙이 SQL 쪽(LOWER/BTRIM)과 같아야 합니다."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")
    _, ingredient_rows = to_staging_rows(outcome.records, source_type=SOURCE_TYPE)

    index = STAGING_RECIPE_INGREDIENT_COLUMNS.index("normalized_name")
    values = [row[index] for row in ingredient_rows]
    assert values == [value.strip().lower() for value in values]


def test_report_shows_match_rate_and_unmatched() -> None:
    """미매칭 재료가 리포트에 건수와 예시까지 나와야 사람이 검토할 수 있습니다."""
    report = LoadReport(
        staged_recipes=2,
        staged_ingredient_lines=8,
        matched_ingredients=6,
        unmatched=[
            UnmatchedIngredient("트러플오일", "트러플 오일 약간", occurrence_count=3, recipe_count=2),
            UnmatchedIngredient("하리사", "하리사 1큰술", occurrence_count=1, recipe_count=1),
        ],
    )

    assert report.match_rate == 0.75
    rendered = report.render()
    assert "6종 매칭 / 2종 미매칭" in rendered
    assert "트러플오일" in rendered
    assert "트러플 오일 약간" in rendered


def test_match_rate_is_none_without_ingredients() -> None:
    """재료가 하나도 없으면 매칭률을 0 으로 보고하지 않고 비워 둡니다."""
    assert LoadReport().match_rate is None
