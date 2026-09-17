"""raw 레코드를 staging 행으로 바꿉니다.

`parsing` 이 가른 이름·수량을 받아 `bulk_insert` 의 STAGING_* 컬럼 순서에 맞춘
튜플로 만듭니다. 컬럼 순서가 거기와 어긋나면 COPY 가 조용히 엉뚱한 칸에 들어갑니다.

    RCP_NM        -> recipe.name
    RCP_WAY2      -> recipe.cooking_method   (끓이기/굽기/볶기/찌기/튀기기/기타)
    RCP_PAT2      -> recipe.tags             (반찬/일품/후식/밥/국&찌개/기타)
    MANUAL01~20   -> recipe_step
    RCP_PARTS_DTLS-> recipe_ingredient
    RCP_SEQ       -> source_recipe_id

`RCP_PAT2` 는 `사람이 쓴 문서/erd_추가내용.md` 가 `rule_type` 예시로 적어 둔
`rcp_pat2` 입니다. 스키마에 전용 컬럼이 없어 `tags` 에 넣습니다.

**영양성분(`INFO_*`)은 넣지 않습니다.** 이번 스프린트 제외 항목 3번입니다.

DB 에 닿지 않습니다. 적재는 `loader` 가 합니다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_pipeline.batch.raw_source import RawDataset, iter_records
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import ingredient_match_key, normalize_cooking_method
from data_pipeline.load.recipe_catalog.parsing import (
    _quantity,
    _text,
    _unit,
    match_candidates,
    parse_ingredients,
)

SOURCE_TYPE = "MFDS_COOKRCP01"

# 이 컬럼들이 다 있어야 이 데이터셋으로 봅니다.
REQUIRED_COLUMNS = ("RCP_SEQ", "RCP_NM", "RCP_PARTS_DTLS")

MAX_STEPS = 20

# 조리법 표기가 우리 열거값과 다른 것만 맞춰 줍니다. 나머지는 normalize_cooking_method 가 처리합니다.
_WAY_ALIASES = {"기타": None}


@dataclass(slots=True)
class RecipeRows:
    """COPY 로 밀어넣을 행 묶음. 컬럼 순서는 `bulk_insert` 의 STAGING_* 와 같습니다."""

    recipes: list[tuple[Any, ...]] = field(default_factory=list)
    steps: list[tuple[Any, ...]] = field(default_factory=list)
    ingredients: list[tuple[Any, ...]] = field(default_factory=list)
    # `003_insert_recipe_ingredient.sql` 이 이 표로 FK 를 채웁니다.
    matches: list[tuple[Any, ...]] = field(default_factory=list)
    skipped: int = 0
    unmatched: dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """적재할 것이 하나도 없는지."""
        return not self.recipes

    def render(self) -> str:
        """사람이 읽을 요약."""
        matched = len(self.ingredients)
        total = matched + sum(self.unmatched.values())
        rate = f"{matched / total * 100:.0f}%" if total else "-"
        lines = [
            f"레시피      {len(self.recipes)}건 (건너뜀 {self.skipped})",
            f"조리 단계   {len(self.steps)}행",
            f"재료 연결   {matched}/{total}행 ({rate})",
        ]
        if self.unmatched:
            top = sorted(self.unmatched.items(), key=lambda item: -item[1])[:10]
            lines.append("미매칭 상위 — " + ", ".join(f"{name} {count}" for name, count in top))
            lines.append(f"미매칭 고유 재료명 {len(self.unmatched)}종")
        return "\n".join(lines)


def write_records(datasets: list[RawDataset], settings: Settings | None = None) -> Path:
    """3단계(`resolve`)가 읽을 수 있게 재료명을 2단계 산출물 형식으로 남깁니다.

    `resolve.collect_names` 가 `data/artifacts/records/*.jsonl` 을 훑어 매칭할 이름을
    모읍니다. 이 데이터는 2단계를 거치지 않으므로 그 자리를 여기서 채웁니다.
    같은 형식을 쓰면 `resolve` 를 고치지 않아도 됩니다.

        uv run data-pipeline load-recipes            # 이 파일이 생깁니다
        uv run data-pipeline resolve --job r2        # 남은 이름만 LLM 요청 생성
        uv run data-pipeline submit  --stage resolve --job r2
        uv run data-pipeline collect --stage resolve --job r2 --wait
        uv run data-pipeline load-recipes --apply    # 늘어난 매칭으로 다시 적재
    """
    settings = settings or get_settings()
    directory = settings.artifacts_dir / "records"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cookrcp01.jsonl"

    with path.open("w", encoding="utf-8") as handle:
        for dataset in datasets:
            for record in iter_records(dataset):
                payload = record.payload
                source_id = _text(payload.get("RCP_SEQ"))
                if not source_id:
                    continue
                ingredients = [
                    {
                        "raw_text": f"{name} {quantity}".strip(),
                        "name": name,
                        "normalized_name": name,
                    }
                    for name, quantity in parse_ingredients(_text(payload.get("RCP_PARTS_DTLS")))
                ]
                line = {
                    "source_recipe_id": source_id,
                    "name": _text(payload.get("RCP_NM")),
                    "ingredients": ingredients,
                    "_entity_key": source_id,
                    "_dataset": SOURCE_TYPE,
                }
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def build_recipe_rows(datasets: list[RawDataset], lookup: dict[str, int]) -> RecipeRows:
    """parquet 을 staging 행으로 바꿉니다.

    `lookup` 은 `resolve.fetch_match_lookup()` 이 주는 `매칭키 -> ingredient_id` 입니다.
    정확히 일치하는 것만 연결하고, 나머지는 보고만 합니다. **추측해서 붙이지 않습니다.**
    """
    rows = RecipeRows()
    matched: dict[str, tuple[int, str]] = {}

    for dataset in datasets:
        for record in iter_records(dataset):
            payload = record.payload
            source_id = _text(payload.get("RCP_SEQ"))
            name = _text(payload.get("RCP_NM"))
            if not source_id or not name:
                rows.skipped += 1
                continue

            dish_type = _text(payload.get("RCP_PAT2"))
            way = _text(payload.get("RCP_WAY2"))
            cooking_method = normalize_cooking_method(_WAY_ALIASES.get(way, way))

            rows.recipes.append(
                (
                    SOURCE_TYPE,
                    source_id,
                    name,
                    # 원천에 줄글 설명이 없습니다. 저염 조리 팁(RCP_NA_TIP)이 그나마 가까워
                    # 설명 자리에 넣습니다. 없으면 null 입니다.
                    _text(payload.get("RCP_NA_TIP")) or None,
                    None,  # cuisine_type
                    None,  # difficulty: 원천에 없습니다
                    None,  # prep_time_min
                    None,  # cook_time_min: 원천에 없습니다
                    None,  # servings
                    cooking_method,
                    json.dumps({}, ensure_ascii=False),  # nutrition 은 JSONB 라 문자열로
                    # tags 는 TEXT[] 라 리스트로 넘깁니다. RCP_PAT2(요리종류)가 여기 들어갑니다.
                    [dish_type] if dish_type else [],
                    _text(payload.get("ATT_FILE_NO_MAIN")) or None,
                )
            )

            for index in range(1, MAX_STEPS + 1):
                instruction = _text(payload.get(f"MANUAL{index:02d}"))
                if not instruction:
                    continue
                # 원문이 `1. 손질된 새우를...a` 처럼 번호와 꼬리 문자를 답니다.
                instruction = re.sub(r"^\d+\.\s*", "", instruction)
                instruction = re.sub(r"[a-z]$", "", instruction).strip()
                rows.steps.append(
                    (
                        SOURCE_TYPE,
                        source_id,
                        index,
                        instruction,
                        _text(payload.get(f"MANUAL_IMG{index:02d}")) or None,
                    )
                )

            for line_no, (ingredient_name, quantity) in enumerate(
                parse_ingredients(_text(payload.get("RCP_PARTS_DTLS"))), start=1
            ):
                key, ingredient_id = None, None
                for candidate in match_candidates(ingredient_name):
                    candidate_key = ingredient_match_key(candidate)
                    if candidate_key in lookup:
                        key, ingredient_id = candidate_key, lookup[candidate_key]
                        break
                if ingredient_id is None or key is None:
                    rows.unmatched[ingredient_name] = rows.unmatched.get(ingredient_name, 0) + 1
                    continue
                rows.ingredients.append(
                    (
                        SOURCE_TYPE,
                        source_id,
                        line_no,
                        f"{ingredient_name} {quantity}".strip(),
                        ingredient_name,
                        key,
                        _quantity(quantity),
                        _unit(quantity),
                        # 원천이 필수/선택을 구분하지 않습니다. 전부 필수로 둡니다.
                        True,
                        None,
                    )
                )
                matched[key] = (ingredient_id, ingredient_name)

    rows.matches = [(key, ingredient_id, name, "exact", 1.0) for key, (ingredient_id, name) in sorted(matched.items())]
    return rows
