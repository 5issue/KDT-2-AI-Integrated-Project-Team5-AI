"""적재용 행 변환 테스트. DB 없이 순수 변환만 확인합니다."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests_helpers import write_jsonl

from data_pipeline.config import Settings
from data_pipeline.load.bulk_insert import (
    STAGING_MATCH_COLUMNS,
    STAGING_RECIPE_COLUMNS,
    STAGING_RECIPE_INGREDIENT_COLUMNS,
    STAGING_STORAGE_COLUMNS,
    build_staging_rows,
)

MATCHES = {"멥쌀": 41, "마늘": 156, "버터": 7}
MATCH_META = {
    "멥쌀": {"matched_name": "멥쌀", "method": "exact", "confidence": 1.0},
    "마늘": {"matched_name": "마늘", "method": "exact", "confidence": 1.0},
    "버터": {"matched_name": "버터", "method": "llm", "confidence": 0.92},
}

RECIPE_RECORD = {
    "_dataset": "korean_recipe_ingredients",
    "_entity_key": "흰밥",
    "source_recipe_id": "흰밥",
    "name": "흰밥",
    "description": "기본 쌀밥",
    "cuisine_type": "한식",
    "difficulty": "EASY",
    "prep_time_min": None,
    "cook_time_min": 30,
    "servings": 2,
    "cooking_method": "밥짓기",
    "tags": ["한식", " "],
    "nutrition": {"calories_kcal": 300, "protein_g": None},
    "ingredients": [
        {
            "raw_text": "멥쌀 2컵",
            "name": "멥쌀",
            "normalized_name": "멥쌀",
            "quantity": 2,
            "unit": "컵",
            "is_required": True,
            "purpose": None,
        },
        {
            "raw_text": "다진 마늘 1큰술",
            "name": "다진 마늘",
            "normalized_name": "마늘",
            "quantity": 1,
            "unit": "큰술",
            "is_required": False,
            "purpose": "양념",
        },
        {
            "raw_text": "두부 반 모",
            "name": "두부",
            "normalized_name": "두부",
            "quantity": 0.5,
            "unit": "모",
            "is_required": True,
            "purpose": None,
        },
    ],
}
STORAGE_RECORD = {
    "_dataset": "storage_guide",
    "_entity_key": "fk_1",
    "source_item_id": "fk_1",
    "source_food_name": "Butter",
    "source_food_subtitle": None,
    "food_name_ko": "버터",
    "normalized_name": "버터",
    "rules": [
        {
            "source_slot": "pantry",
            "duration_min": None,
            "duration_max": None,
            "duration_unit": None,
            "duration_text": "실온 1-2일",
            "storage_tips": "오래 두지 않는다",
        },
        {
            "source_slot": "dop_refrigerate",
            "duration_min": 1,
            "duration_max": 2,
            "duration_unit": "Months",
            "duration_text": "구매 후 1-2개월",
            "storage_tips": None,
        },
    ],
}


def seed(settings: Settings) -> None:
    """2단계 산출물을 준비합니다."""
    records = settings.artifacts_dir / "records"
    write_jsonl(records / "korean_recipe_ingredients.jsonl", [RECIPE_RECORD])
    write_jsonl(records / "storage_guide.jsonl", [STORAGE_RECORD])


def test_row_widths_match_copy_columns(tmp_settings: Settings) -> None:
    """튜플 길이가 COPY 컬럼 수와 맞아야 asyncpg 가 받아줍니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    assert all(len(row) == len(STAGING_RECIPE_COLUMNS) for row in rows.recipes)
    assert all(len(row) == len(STAGING_RECIPE_INGREDIENT_COLUMNS) for row in rows.recipe_ingredients)
    assert all(len(row) == len(STAGING_STORAGE_COLUMNS) for row in rows.storage)
    assert all(len(row) == len(STAGING_MATCH_COLUMNS) for row in rows.matches)


def test_source_type_defaults_to_dataset_name(tmp_settings: Settings) -> None:
    """레시피 자연키는 (source_type, source_recipe_id) 입니다. 출처가 섞이지 않게 데이터셋 이름을 씁니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    recipe = rows.recipes[0]
    assert recipe[STAGING_RECIPE_COLUMNS.index("source_type")] == "korean_recipe_ingredients"
    assert recipe[STAGING_RECIPE_COLUMNS.index("source_id")] == "흰밥"


def test_unmatched_ingredient_is_skipped_and_counted(tmp_settings: Settings) -> None:
    """매칭 안 된 재료는 FK 를 못 채우므로 빠지고, 건수가 보고돼야 합니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    normalized = [row[STAGING_RECIPE_INGREDIENT_COLUMNS.index("normalized_name")] for row in rows.recipe_ingredients]
    assert normalized == ["멥쌀", "마늘"]  # 두부는 매칭 실패
    assert rows.skipped_ingredients == 1


def test_unmatched_storage_item_skips_all_its_rules(tmp_settings: Settings) -> None:
    """품목이 매칭되지 않으면 그 품목의 슬롯 전부가 빠집니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", {"멥쌀": 41}, {})

    assert rows.storage == []
    assert rows.skipped_storage == 2


def test_storage_columns_are_derived_from_slot(tmp_settings: Settings) -> None:
    """location/context 는 slot 에서 결정적으로 나와야 CHECK 제약을 통과합니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    slot_index = STAGING_STORAGE_COLUMNS.index("source_slot")
    location_index = STAGING_STORAGE_COLUMNS.index("storage_location")
    context_index = STAGING_STORAGE_COLUMNS.index("storage_context")
    derived = {(row[slot_index], row[location_index], row[context_index]) for row in rows.storage}

    assert derived == {
        ("pantry", "상온", "일반"),
        ("dop_refrigerate", "냉장", "구매후"),
    }


def test_numeric_columns_are_decimal(tmp_settings: Settings) -> None:
    """asyncpg 의 NUMERIC 코덱은 float 을 거부하므로 Decimal 이어야 합니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    assert isinstance(rows.recipes[0][STAGING_RECIPE_COLUMNS.index("servings")], Decimal)
    assert isinstance(rows.recipe_ingredients[0][STAGING_RECIPE_INGREDIENT_COLUMNS.index("quantity")], Decimal)
    durations = [row[STAGING_STORAGE_COLUMNS.index("duration_min")] for row in rows.storage]
    assert any(isinstance(value, Decimal) for value in durations)


def test_jsonb_and_array_columns(tmp_settings: Settings) -> None:
    """nutrition 은 JSON 문자열, tags 는 list[str] 이어야 합니다. null 영양소는 버립니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    nutrition = rows.recipes[0][STAGING_RECIPE_COLUMNS.index("nutrition")]
    tags = rows.recipes[0][STAGING_RECIPE_COLUMNS.index("tags")]
    assert nutrition == '{"calories_kcal": 300}'
    assert tags == ["한식"]  # 공백뿐인 태그는 버립니다


def test_match_metadata_is_carried(tmp_settings: Settings) -> None:
    """정확 일치와 LLM 매칭을 staging 에서 구분할 수 있어야 검증이 됩니다."""
    seed(tmp_settings)
    rows = build_staging_rows(tmp_settings.artifacts_dir / "records", MATCHES, MATCH_META)

    methods = {row[0]: row[STAGING_MATCH_COLUMNS.index("method")] for row in rows.matches}
    assert methods == {"멥쌀": "exact", "마늘": "exact", "버터": "llm"}


def test_translated_recipe_below_threshold_is_skipped(tmp_path: Path) -> None:
    """재료가 절반도 안 붙은 번역 레시피는 '부족 재료' 계산이 무의미합니다."""
    records = tmp_path / "records"
    records.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_recipe_id": "r1",
        "name": "사과 파이",
        "name_original": "Apple Pie",
        "ingredients": [
            {"raw_text": "a", "name": "사과", "normalized_name": "사과", "is_required": True},
            {"raw_text": "b", "name": "버터", "normalized_name": "버터", "is_required": True},
            {"raw_text": "c", "name": "밀가루", "normalized_name": "밀가루", "is_required": True},
        ],
        "steps": [],
    }
    write_jsonl(records / "recipes.jsonl", [payload])

    # 3개 중 1개만 매칭 = 33% < 70%
    rows = build_staging_rows(records, {"사과": 1}, min_match_rate=0.7)
    assert rows.recipes == []
    assert rows.skipped_recipes == 1

    # 임계값을 낮추면 들어옵니다.
    rows = build_staging_rows(records, {"사과": 1}, min_match_rate=0.3)
    assert len(rows.recipes) == 1
    assert rows.skipped_recipes == 0


def test_korean_recipe_is_not_filtered(tmp_path: Path) -> None:
    """한국어 원본 레시피는 MVP 우선 적재 대상이라 매칭률로 거르지 않습니다."""
    records = tmp_path / "records"
    records.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_recipe_id": "k1",
        "name": "흰밥",
        "name_original": None,
        "ingredients": [
            {"raw_text": "멥쌀", "name": "멥쌀", "normalized_name": "멥쌀", "is_required": True},
            {"raw_text": "물", "name": "물", "normalized_name": "물", "is_required": True},
        ],
        "steps": [],
    }
    write_jsonl(records / "recipes.jsonl", [payload])

    # 하나도 안 붙어도 적재합니다.
    rows = build_staging_rows(records, {}, min_match_rate=0.7)
    assert len(rows.recipes) == 1
    assert rows.skipped_recipes == 0


def test_partial_duration_is_nulled_to_satisfy_check(tmp_path: Path) -> None:
    """duration 은 셋 다 있거나 셋 다 없어야 합니다(ck_storage_guideline_duration_complete).

    원본 unit_source 가 'When Ripe' 처럼 단위가 아닌 문구여서 수치 없이 단위만 채워진
    행이 23개 있었고, 그 한 부류 때문에 적재 전체가 롤백됐습니다.
    """
    records = tmp_path / "records"
    records.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_item_id": "fk_249",
        "source_food_name": "Apricots",
        "normalized_name": "살구",
        "rules": [
            {
                "source_slot": "pantry",
                "duration_min": None,
                "duration_max": None,
                "duration_unit": "When Ripe",
                "duration_text": "익었을 때",
            },
            {
                "source_slot": "freeze",
                "duration_min": 6.0,
                "duration_max": 9.0,
                "duration_unit": "Months",
                "duration_text": "6-9 개월",
            },
        ],
    }
    write_jsonl(records / "storage_guide.jsonl", [payload])

    rows = build_staging_rows(records, {"살구": 5})
    partial, complete = rows.storage[0], rows.storage[1]

    # 컬럼 순서: ... duration_min, duration_max, duration_unit, duration_text
    assert (partial[7], partial[8], partial[9]) == (None, None, None)
    assert partial[10] == "익었을 때", "사람이 읽을 표기는 남아야 합니다"
    assert (complete[8], complete[9]) == (Decimal("9.00"), "개월"), "단위는 한국어 한 벌로 모읍니다"
