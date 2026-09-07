"""LLM 출력 스키마 검증."""

from __future__ import annotations

from typing import Any

import pytest

from data_pipeline.schemas import ParsedRecipe, ParsedRecipeIngredient, strict_json_schema


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


def test_parsed_recipe_schema_is_strict() -> None:
    """ParsedRecipe 의 JSON Schema 는 중첩 모델까지 strict 여야 합니다."""
    assert_strict(strict_json_schema(ParsedRecipe))


def test_parsed_recipe_rejects_unknown_field() -> None:
    """스키마에 없는 키가 오면 검증에서 걸러집니다."""
    with pytest.raises(ValueError):
        ParsedRecipeIngredient.model_validate(
            {
                "raw_text": "마늘 1큰술",
                "name": "마늘",
                "normalized_name": "마늘",
                "is_required": True,
                "role": "SEASONING",
                "surprise": "허용되면 안 됨",
            }
        )


def test_parsed_recipe_rejects_unknown_role() -> None:
    """role 은 정해진 값만 받습니다."""
    with pytest.raises(ValueError):
        ParsedRecipeIngredient.model_validate(
            {
                "raw_text": "마늘",
                "name": "마늘",
                "normalized_name": "마늘",
                "is_required": True,
                "role": "UNKNOWN_ROLE",
            }
        )
