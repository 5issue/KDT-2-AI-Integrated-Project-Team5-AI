"""LLM 파싱 결과 스키마 (Pydantic v2) + OpenAI structured outputs 용 strict JSON Schema 변환.

`ai_context/database_schema.md` v0.1.0 의 recipe / recipe_ingredient / ingredient 컬럼에
1:1 로 대응시켜 두었습니다. LLM 은 여기 정의된 모양으로만 답하고, 파이썬 쪽에서
결정적으로 계산할 수 있는 값(정규화, 파생 컬럼)은 LLM 에게 맡기지 않습니다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Difficulty = Literal["EASY", "MEDIUM", "HARD"]
IngredientRole = Literal["PRIMARY", "SUB", "SEASONING", "GARNISH"]


class StrictModel(BaseModel):
    """structured outputs 로 넘길 모델의 공통 설정."""

    model_config = ConfigDict(extra="forbid")


class ParsedNutrition(StrictModel):
    """recipe.nutrition JSONB 에 들어갈 1인분 기준 영양정보."""

    calories_kcal: float | None = Field(default=None, description="1인분 기준 열량(kcal)")
    carbohydrate_g: float | None = Field(default=None, description="1인분 탄수화물(g)")
    protein_g: float | None = Field(default=None, description="1인분 단백질(g)")
    fat_g: float | None = Field(default=None, description="1인분 지방(g)")
    sodium_mg: float | None = Field(default=None, description="1인분 나트륨(mg)")


class ParsedRecipeIngredient(StrictModel):
    """recipe_ingredient 한 줄 + 매칭할 ingredient 정보."""

    raw_text: str = Field(description="원문에 적힌 재료 표기 그대로")
    name: str = Field(description="재료 이름 (한국어 표기)")
    normalized_name: str = Field(
        description="공백/수식어/브랜드를 제거한 검색용 기본형. 예: '다진 마늘' -> '마늘'",
    )
    quantity: float | None = Field(default=None, description="수량. 원문에 없으면 null")
    unit: str | None = Field(default=None, description="단위(g, ml, 개, 큰술 등). 없으면 null")
    is_required: bool = Field(description="없으면 요리가 성립하지 않는 필수 재료면 true")
    role: IngredientRole = Field(description="재료의 역할")
    purpose: str | None = Field(default=None, description="쓰임새 한 줄 설명. 없으면 null")


class ParsedRecipe(StrictModel):
    """원문 레시피 문서 한 건을 recipe 테이블 모양으로 파싱한 결과."""

    name: str = Field(description="레시피 이름")
    description: str | None = Field(default=None, description="두세 문장 요약. 없으면 null")
    cuisine_type: str | None = Field(default=None, description="한식/양식/중식/일식/기타 중 하나")
    difficulty: Difficulty | None = Field(default=None, description="조리 난이도")
    prep_time_min: int | None = Field(default=None, description="준비 시간(분)")
    cook_time_min: int | None = Field(default=None, description="조리 시간(분)")
    servings: float | None = Field(default=None, description="기준 인분 수")
    cooking_method: str | None = Field(default=None, description="대표 조리법. 예: 볶음, 조림, 구이")
    tags: list[str] = Field(description="검색용 태그. 없으면 빈 배열")
    nutrition: ParsedNutrition | None = Field(default=None, description="영양정보. 원문에 없으면 null")
    ingredients: list[ParsedRecipeIngredient] = Field(description="재료 목록. 최소 1개")


class BatchParseFailure(StrictModel):
    """Batch 결과 한 줄이 실패했을 때 남기는 기록."""

    custom_id: str
    reason: str
    detail: str | None = None


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 모델을 OpenAI structured outputs 의 strict 규칙에 맞는 JSON Schema 로 바꿉니다.

    strict 모드는 모든 object 에 `additionalProperties: false` 와 전체 필드의 `required`
    나열을 요구하고, `default` 를 허용하지 않습니다. Pydantic 기본 출력은 이 조건을
    만족하지 않으므로 여기서 후처리합니다.
    """
    schema = model.model_json_schema()
    _strictify(schema)
    return schema


def _strictify(node: Any) -> None:
    """JSON Schema 트리를 제자리에서 strict 규칙에 맞게 고칩니다."""
    if isinstance(node, list):
        for item in node:
            _strictify(item)
        return
    if not isinstance(node, dict):
        return

    node.pop("default", None)

    if node.get("type") == "object" or "properties" in node:
        properties = node.setdefault("properties", {})
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())

    for value in node.values():
        _strictify(value)
