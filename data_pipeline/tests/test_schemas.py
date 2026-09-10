"""LLM 출력 스키마 검증. strict 모드를 벗어나면 배치가 통째로 실패합니다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from data_pipeline.schemas import (
    DatasetProfile,
    ExtractedIngredientLine,
    ExtractedRecipe,
    ExtractedStorageItem,
    ExtractedStorageRule,
    IngredientMatchBatch,
    strict_json_schema,
)

MODELS = [DatasetProfile, ExtractedRecipe, ExtractedStorageItem, IngredientMatchBatch]


def assert_strict(node: Any, path: str = "#") -> None:
    """모든 object 노드가 structured outputs strict 규칙을 만족하는지 확인합니다."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties 누락"
            assert set(node["required"]) == set(node["properties"]), f"{path}: required 가 전체 필드와 다름"
        assert "default" not in node, f"{path}: strict 모드는 default 를 허용하지 않음"
        for key, value in node.items():
            assert_strict(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            assert_strict(value, f"{path}[{index}]")


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_schema_is_strict(model: type) -> None:
    """중첩 모델까지 strict 여야 합니다."""
    assert_strict(strict_json_schema(model))


def test_storage_slot_enum_is_closed() -> None:
    """슬롯이 열거형이라 모델이 DB CHECK 밖의 값을 낼 수 없습니다."""
    schema = strict_json_schema(ExtractedStorageRule)
    assert set(schema["properties"]["source_slot"]["enum"]) == {
        "pantry",
        "dop_pantry",
        "pantry_after_opening",
        "refrigerate",
        "dop_refrigerate",
        "refrigerate_after_opening",
        "refrigerate_after_thawing",
        "freeze",
        "dop_freeze",
    }


def test_unknown_slot_is_rejected() -> None:
    """열거형 밖의 슬롯은 검증에서 걸립니다."""
    with pytest.raises(ValidationError):
        ExtractedStorageRule.model_validate({"source_slot": "basement", "duration_text": "x"})


def test_unknown_field_is_rejected() -> None:
    """스키마에 없는 키가 오면 걸러집니다."""
    with pytest.raises(ValidationError):
        ExtractedIngredientLine.model_validate(
            {
                "raw_text": "마늘",
                "name": "마늘",
                "normalized_name": "마늘",
                "is_required": True,
                "is_raw_material": True,
                "surprise": "허용되면 안 됨",
            }
        )


def test_profile_requires_row_granularity() -> None:
    """행 단위 판단이 빠지면 2단계에서 묶을 방법이 없습니다."""
    with pytest.raises(ValidationError):
        DatasetProfile.model_validate(
            {
                "dataset": "x",
                "summary": "y",
                "language": "ko",
                "target_tables": ["recipe"],
                "group_by_columns": [],
                "entity_key_columns": [],
                "content_columns": [],
                "companion_datasets": [],
                "loadable": True,
                "column_meanings": [],
                "confidence": 0.5,
            }
        )


def test_ingredient_line_keeps_original_text() -> None:
    """한국어로 정규화하되 원문 표기를 잃지 않아야 검토가 됩니다."""
    line = ExtractedIngredientLine.model_validate(
        {
            "raw_text": "2 tablespoons unsalted butter",
            "name": "무염버터",
            "name_original": "unsalted butter",
            "normalized_name": "버터",
            "quantity": 2,
            "unit": "큰술",
            "is_required": True,
            "is_raw_material": False,
            "purpose": None,
        }
    )
    assert line.name_original == "unsalted butter"
    assert line.normalized_name == "버터"
