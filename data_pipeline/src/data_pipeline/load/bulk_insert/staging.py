"""2·3단계 산출물 -> staging 행 튜플. **DB 를 보지 않습니다.**

레코드 하나가 레시피 한 건이고, 거기서 recipe / recipe_ingredient / recipe_step
세 표의 행이 함께 나옵니다. 보관기준은 슬롯 하나가 행 하나입니다.

여기서 하는 판단은 둘입니다.

- **번역본은 재료 매칭률이 낮으면 버립니다**(`RECIPE_MIN_MATCH_RATE`). 재료가 절반도
  안 붙은 레시피는 "부족 재료" 계산이 무의미해 데모에 못 씁니다. 한국어 원본에는
  이 필터를 걸지 않습니다.
- 수치는 전부 `Decimal` 로 바꿉니다. asyncpg 의 NUMERIC 코덱이 float 를 받지 않습니다.

`collect_rows` 만 파일을 읽습니다. 나머지는 dict 를 받아 튜플을 냅니다.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import (
    derive_storage_columns,
    ingredient_match_key,
    normalize_cooking_method,
    normalize_duration_unit,
)
from data_pipeline.load.bulk_insert.models import StagingRows


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
            normalize_cooking_method(_truncate(payload.get("cooking_method"), 50)),
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
