"""3단계 파이프라인의 LLM 출력 스키마 (Pydantic v2).

각 단계가 무엇을 판단하는지가 여기 스키마에 그대로 드러납니다.

1단계 프로파일  : DatasetProfile   - 이 데이터셋이 뭐고, 어느 테이블로 가고, 행 단위가 뭔지
2단계 추출      : ExtractedRecipe / ExtractedStorageItem - 레코드를 타깃 테이블 모양으로
3단계 해석      : IngredientMatchBatch - 재료명을 기존 마스터 ingredient_id 로

structured outputs strict 모드로 넘기기 때문에 모델이 이 모양을 벗어난 JSON 을 낼 수 없습니다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from data_pipeline.domain import STORAGE_SLOTS, TARGET_TABLES

TargetTable = Literal["recipe", "recipe_ingredient", "recipe_step", "storage_guideline", "none"]
Language = Literal["ko", "en", "mixed", "unknown"]
Difficulty = Literal["EASY", "MEDIUM", "HARD"]
RowsPerEntity = Literal["one", "many"]
StorageSlot = Literal[
    "pantry",
    "dop_pantry",
    "pantry_after_opening",
    "refrigerate",
    "dop_refrigerate",
    "refrigerate_after_opening",
    "refrigerate_after_thawing",
    "freeze",
    "dop_freeze",
]

# domain.SLOT_DERIVATION 과 어긋나면 적재 시점에 CHECK 제약으로 터지므로 여기서 고정합니다.
assert set(STORAGE_SLOTS) == set(StorageSlot.__args__), "StorageSlot 이 domain.SLOT_DERIVATION 과 다릅니다"
assert set(TARGET_TABLES) <= set(TargetTable.__args__), "TargetTable 이 domain.TARGET_TABLES 와 다릅니다"


class StrictModel(BaseModel):
    """structured outputs 로 넘길 모델의 공통 설정."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 1단계: 데이터셋 프로파일
# ---------------------------------------------------------------------------


class ColumnMeaning(StrictModel):
    """컬럼 하나의 뜻과 타깃 필드."""

    column: str = Field(description="원본 컬럼명 그대로")
    meaning: str = Field(description="이 컬럼이 무엇을 담고 있는지 한 줄로")
    target_field: str | None = Field(
        default=None,
        description="대응하는 타깃 컬럼. 'recipe.name' 처럼 테이블.컬럼 형식. 없으면 null",
    )


class DatasetProfile(StrictModel):
    """데이터셋 하나를 어떻게 다룰지에 대한 판단."""

    dataset: str = Field(description="데이터셋 이름 (입력으로 준 이름 그대로)")
    summary: str = Field(description="이 데이터셋이 무엇인지 두세 문장")
    language: Language = Field(description="주된 언어")
    target_tables: list[TargetTable] = Field(
        description="적재 대상 테이블. 레시피처럼 두 테이블을 동시에 만들면 둘 다 적는다. 없으면 ['none']",
    )
    rows_per_entity: RowsPerEntity = Field(
        description="한 엔티티가 한 행이면 one, 여러 행에 걸쳐 있으면 many",
    )
    group_by_columns: list[str] = Field(
        description="rows_per_entity 가 many 일 때 엔티티를 묶는 컬럼. one 이면 빈 배열",
    )
    entity_key_columns: list[str] = Field(
        description="엔티티를 식별하는 자연키 컬럼. source_recipe_id / source_item_id 재료가 된다",
    )
    content_columns: list[str] = Field(
        description="실제 내용이 들어 있어 2단계에서 반드시 봐야 하는 컬럼",
    )
    companion_datasets: list[str] = Field(
        description="같은 엔티티를 다루어 함께 봐야 하는 다른 데이터셋 이름. 없으면 빈 배열",
    )
    loadable: bool = Field(description="이번 범위에서 적재 가능한 데이터인지")
    skip_reason: str | None = Field(default=None, description="loadable 이 false 인 이유. true 면 null")
    column_meanings: list[ColumnMeaning] = Field(description="모든 컬럼에 대한 해석")
    confidence: float = Field(description="이 판단에 대한 확신도 0~1")


# ---------------------------------------------------------------------------
# 2단계: 추출 (레시피)
# ---------------------------------------------------------------------------


class ExtractedNutrition(StrictModel):
    """recipe.nutrition JSONB 에 들어갈 1인분 기준 영양정보."""

    calories_kcal: float | None = Field(default=None, description="1인분 열량(kcal)")
    carbohydrate_g: float | None = Field(default=None, description="1인분 탄수화물(g)")
    protein_g: float | None = Field(default=None, description="1인분 단백질(g)")
    fat_g: float | None = Field(default=None, description="1인분 지방(g)")
    sodium_mg: float | None = Field(default=None, description="1인분 나트륨(mg)")


class ExtractedIngredientLine(StrictModel):
    """recipe_ingredient 한 줄. 재료명은 한국어로 정규화합니다."""

    raw_text: str = Field(description="원문에 적힌 재료 표기 그대로")
    name: str = Field(description="재료 이름(한국어). 영어 원문이면 한국어로 옮긴다")
    name_original: str | None = Field(default=None, description="원문이 한국어가 아니면 원표기. 아니면 null")
    normalized_name: str = Field(
        description="수식어/브랜드/손질상태를 뺀 한국어 기본형. 예: 'unsalted butter' -> '버터', '다진 마늘' -> '마늘'",
    )
    quantity: float | None = Field(default=None, description="수량. 원문에 없으면 null")
    unit: str | None = Field(
        default=None,
        description="단위. 한국어로 통일한다(큰술/작은술/컵/개/장/쪽). g, ml 같은 국제단위는 그대로. 없으면 null",
    )
    is_required: bool = Field(description="없으면 요리가 성립하지 않는 필수 재료면 true")
    is_raw_material: bool = Field(
        description="용어 기준의 원재료(가공되지 않은 순수 원료)면 true, 가공품/성분이면 false",
    )
    purpose: str | None = Field(
        default=None,
        description="용어 기준의 용도 분류. 예: 국물찌개용, 구이스테이크용, 반찬무침용, 양념. 없으면 null",
    )


class ExtractedRecipeStep(StrictModel):
    """recipe_step 한 줄. 조리 순서를 단계로 분리합니다.

    DB CHECK 가 instruction 과 image_url 중 하나는 있을 것을 요구합니다.
    둘 다 비면 적재 시점에 걸리므로 여기서 만들지 않습니다.
    """

    step_no: int = Field(description="1부터 시작하는 표시 순서. 원천 번호가 아니라 정리된 순서다")
    instruction: str | None = Field(
        default=None,
        description="단계 설명(한국어). 원문의 번호 접두사(1., 2))는 떼고 내용만. 사진만 있으면 null",
    )
    image_url: str | None = Field(default=None, description="단계 사진 URL. 원문에 있으면 그대로, 없으면 null")


class ExtractedRecipe(StrictModel):
    """원본 레코드 하나(또는 그룹)를 recipe + recipe_ingredient + recipe_step 모양으로 변환한 결과."""

    source_recipe_id: str = Field(description="원본 자연키. 프로파일의 entity_key_columns 값을 조합해 만든다")
    name: str = Field(description="레시피 이름(한국어)")
    name_original: str | None = Field(default=None, description="원문이 한국어가 아니면 원표기")
    description: str | None = Field(
        default=None,
        description="어떤 음식인지 두세 문장. URL 이나 출처 표기는 넣지 않는다. 설명이 없으면 null",
    )
    cuisine_type: str | None = Field(default=None, description="한식/양식/중식/일식/기타")
    difficulty: Difficulty | None = Field(default=None, description="조리 난이도")
    prep_time_min: int | None = Field(default=None, description="준비 시간(분). 원문에 없으면 null")
    cook_time_min: int | None = Field(default=None, description="조리 시간(분). 원문에 없으면 null")
    servings: float | None = Field(default=None, description="기준 인분 수")
    cooking_method: str | None = Field(default=None, description="대표 조리법. 예: 볶음, 조림, 구이, 끓이기")
    tags: list[str] = Field(description="용어 기준의 용도/TPO 태그. 없으면 빈 배열")
    nutrition: ExtractedNutrition | None = Field(default=None, description="영양정보. 원문에 없으면 null")
    image_url: str | None = Field(default=None, description="레시피 대표 사진 URL. 원문에 없으면 null")
    ingredients: list[ExtractedIngredientLine] = Field(description="재료 목록")
    steps: list[ExtractedRecipeStep] = Field(
        description="조리 단계. 원문에 조리 순서가 있으면 단계로 쪼갠다. 없으면 빈 배열",
    )
    confidence: float = Field(description="추출 확신도 0~1")
    reason: str = Field(description="판단 근거 한 줄. 특히 원문에 없어 null 로 둔 값이 있으면 적는다")


# ---------------------------------------------------------------------------
# 2단계: 추출 (보관 기준)
# ---------------------------------------------------------------------------


class ExtractedStorageRule(StrictModel):
    """storage_guideline 한 줄. 보관 슬롯 하나에 대응합니다."""

    source_slot: StorageSlot = Field(description="보관 슬롯. DOP 는 구매일 기준을 뜻한다")
    duration_min: float | None = Field(default=None, description="최소 보관 기간. 수치가 없으면 null")
    duration_max: float | None = Field(default=None, description="최대 보관 기간. 수치가 없으면 null")
    duration_unit: str | None = Field(default=None, description="기간 단위(Days, Weeks, Months 등). 없으면 null")
    duration_text: str = Field(
        description="사람이 읽을 기간 표기. 수치가 없으면 원문 문구를 그대로 살린다. 비울 수 없다",
    )
    storage_tips: str | None = Field(default=None, description="보관 팁(한국어). 없으면 null")


class ExtractedStorageItem(StrictModel):
    """한 품목의 보관 기준 전체. 슬롯이 여러 개라 묶어서 한 번에 받습니다."""

    source_item_id: str = Field(description="원본 품목 식별자")
    source_food_name: str = Field(description="원문 품목명 그대로")
    source_food_subtitle: str | None = Field(default=None, description="원문 부제. 없으면 null")
    food_name_ko: str = Field(description="한국어 품목명. 3단계에서 재료 마스터 매칭에 쓰인다")
    normalized_name: str = Field(description="수식어를 뺀 한국어 기본형. 매칭 정확도를 높이기 위한 값")
    rules: list[ExtractedStorageRule] = Field(description="보관 슬롯별 규칙")
    confidence: float = Field(description="추출 확신도 0~1")
    reason: str = Field(description="판단 근거 한 줄")


# ---------------------------------------------------------------------------
# 3단계: 재료 마스터 매칭
# ---------------------------------------------------------------------------


class IngredientMatch(StrictModel):
    """재료명 하나에 대한 매칭 결과."""

    source_name: str = Field(description="매칭을 요청한 이름 그대로")
    ingredient_id: int | None = Field(default=None, description="후보 목록에 있는 id. 없으면 null")
    matched_name: str | None = Field(default=None, description="고른 마스터 재료 이름. 없으면 null")
    confidence: float = Field(description="매칭 확신도 0~1. 애매하면 낮게 준다")
    reason: str = Field(description="왜 그 재료로 봤는지 한 줄")


class IngredientMatchBatch(StrictModel):
    """여러 재료명을 한 요청에 담아 마스터 목록 토큰을 아낍니다."""

    matches: list[IngredientMatch] = Field(description="요청한 이름마다 하나씩")


# ---------------------------------------------------------------------------
# strict JSON Schema 변환
# ---------------------------------------------------------------------------


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 모델을 OpenAI structured outputs 의 strict 규칙에 맞는 JSON Schema 로 바꿉니다.

    strict 모드는 모든 object 에 `additionalProperties: false` 와 전체 필드의 `required`
    나열을 요구하고, `default` 를 허용하지 않습니다.
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
