"""식약처 조리식품 레시피 DB(COOKRCP01) 적재.

`data/raw/cookrcp01_all.parquet` 1,156건입니다. 기존 한국어 레시피 30건의 38배라,
"앞으로 한국어 raw 만 적재" 방침의 실제 재료가 됩니다.

## LLM 을 쓰지 않습니다

`product_raw.parquet` 과 같은 이유입니다. 이 데이터는 **이미 구조화되어** 들어옵니다.
컬럼 의미를 판단할 필요가 없으니 1~3단계를 태울 이유가 없습니다.

    RCP_NM        -> recipe.name
    RCP_WAY2      -> recipe.cooking_method   (끓이기/굽기/볶기/찌기/튀기기/기타)
    RCP_PAT2      -> recipe.tags             (반찬/일품/후식/밥/국&찌개/기타)
    MANUAL01~20   -> recipe_step
    RCP_PARTS_DTLS-> recipe_ingredient       (아래 파싱)
    RCP_SEQ       -> source_recipe_id

`RCP_PAT2` 는 `ai_context/사람이 쓴 문서/erd_추가내용.md` 가 `rule_type` 예시로 적어 둔
`rcp_pat2` 입니다. 스키마에 전용 컬럼이 없어 `tags` 에 넣습니다.

## 재료 파싱

`RCP_PARTS_DTLS` 는 자유 문자열이지만 형식이 일정합니다.

    ●방울토마토 소박이 :
    방울토마토 150g(5개), 양파 10g(3×1cm), 부추 10g(5줄기)
    ●양념장 :
    고춧가루 4g(1작은술), 멸치액젓 3g(2/3작은술)

`●`·`·`·줄바꿈으로 구획을 나누고, 구획 제목(`... :`)을 버리고, 쉼표로 끊은 뒤
이름과 수량을 가릅니다. 판단이 아니라 규칙이라 파이썬에 둡니다.

**영양성분(`INFO_*`)은 넣지 않습니다.** 이번 스프린트 제외 항목 3번입니다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from data_pipeline.batch.raw_source import RawDataset, iter_records
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import ingredient_match_key, normalize_cooking_method
from data_pipeline.load.bulk_insert import (
    STAGING_MATCH_COLUMNS,
    STAGING_RECIPE_COLUMNS,
    STAGING_RECIPE_INGREDIENT_COLUMNS,
    STAGING_RECIPE_STEP_COLUMNS,
    LoadReport,
    load_connection_scope,
    run_sql_file,
)

SOURCE_TYPE = "MFDS_COOKRCP01"

# 이 컬럼들이 다 있어야 이 데이터셋으로 봅니다.
REQUIRED_COLUMNS = ("RCP_SEQ", "RCP_NM", "RCP_PARTS_DTLS")

MAX_STEPS = 20

# 원본에 HTML 태그가 섞여 있습니다(`<br>` 42회, `<br />` 6회, `<strong>` 등).
# 떼지 않으면 `<br>` 이 재료명으로 잡혀 미매칭 목록을 더럽힙니다. 실제로 21건 나왔습니다.
_HTML_TAG = re.compile(r"<[^>]{1,20}>")

# 구획을 나누는 것들. `●양념장 :` 같은 소제목이 이 뒤에 옵니다.
# `•` 도 구획 기호로 쓰입니다(`• [가정 간편식 재료] ...`).
_SECTION = re.compile(r"[●·▶∙•]|\n")

# `연두부 75g(3/4모)` 에서 이름만. 수량이 없는 `참깨 약간` 도 받습니다.
_ITEM = re.compile(r"^(?P<name>[^0-9(]+?)\s*(?P<qty>[\d./⅓⅔¼½¾]+\s*[a-zA-Z가-힣]*)?\s*(\(.*\))?$")

# `[1인분]`, `2인분 기준` 같은 머리말.
_PORTION = re.compile(r"^\[[^\]]*\]|^\d+인분\s*(기준)?\s*[:：]?")

# `양념장 : ` 같은 구획 제목.
_SECTION_TITLE = re.compile(r"^[^:：]{1,14}\s*[:：]\s*")

# 수량이 아니라 상태를 적은 꼬리. 재료명에서 뗍니다.
_TRAILING = re.compile(r"\s*(약간|적당량|조금|소량)$")

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


def _text(value: Any) -> str:
    """parquet 이 'None' 문자열로 싣고 오는 빈 값을 정리합니다."""
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() in {"", "none", "null", "nan", "-"} else text


def parse_ingredients(raw: str) -> list[tuple[str, str]]:
    """`RCP_PARTS_DTLS` 에서 (재료명, 수량표기) 목록을 뽑습니다.

    수량은 원문 표기를 그대로 둡니다. `75g` 를 숫자로 쪼개는 것은 부피->무게 환산과
    얽혀 있어 이번 스프린트 제외 항목(2번)입니다.
    """
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    # HTML 을 줄바꿈으로 바꿉니다. `<br>` 이 구획 구분자 노릇을 하고 있습니다.
    cleaned = _HTML_TAG.sub("\n", raw or "")

    for chunk in _SECTION.split(cleaned):
        chunk = chunk.strip()
        if not chunk or chunk.endswith((":", "：")):
            continue
        chunk = _PORTION.sub("", chunk).strip()
        chunk = _SECTION_TITLE.sub("", chunk).strip()

        for part in chunk.split(","):
            part = part.strip()
            if not part:
                continue
            match = _ITEM.match(part)
            name = (match.group("name") if match else part).strip()
            quantity = (match.group("qty") or "").strip() if match else ""
            name = _TRAILING.sub("", name).strip()

            # **한 글자도 받습니다.** 한국어 재료에는 한 음절이 흔합니다 —
            # 마스터 1,028종 중 41종이 한 글자이고(물·꿀·무·배·쌀·파·잣·밤·김),
            # 버리면 이 데이터에서만 648줄이 사라집니다.
            # 매칭은 어차피 마스터에 있는 이름만 붙으므로 오탐 위험이 낮습니다.
            if not name or name[0].isdigit():
                continue
            key = ingredient_match_key(name)
            if key in seen:
                continue
            seen.add(key)
            items.append((name, quantity))
    return items


# 손질 상태 접두어. `다진 마늘` 과 `마늘` 은 같은 재료입니다.
#
# **상태만 뗍니다.** `고춧가루`/`마늘가루` 처럼 가루는 별도 재료라 건드리지 않습니다
# (정규화 가이드 3.4). `후춧가루 -> 후추`, `닭가슴살 -> 닭고기` 같은 것은 판단이 필요해
# 여기서 하지 않고 3단계 `resolve` 로 넘깁니다. 규칙과 판단을 섞지 않습니다.
_PREP_PREFIX = re.compile(
    r"^(굵게\s*다진|잘게\s*다진|곱게\s*다진|다진|다져놓은|"
    r"채\s*썬|채썰은|어슷\s*썬|송송\s*썬|편\s*썬|저민|썬|"
    r"삶은|데친|구운|볶은|찐|절인|말린|건조|불린|깐|손질한|손질|다듬은)\s*"
)
_PREP_SUFFIX = re.compile(r"(다진\s*것|채썬\s*것|간\s*것|썰은\s*것)$")


def match_candidates(name: str) -> list[str]:
    """이 재료명으로 시도해 볼 표기들. 앞에 있는 것부터 씁니다.

    원문을 먼저 보고, 안 맞으면 손질 상태를 뗀 형태를 봅니다.
    `올리브오일`/`올리브유` 처럼 같은 뜻의 표기 차이도 함께 봅니다.
    """
    seen: list[str] = []

    def add(value: str) -> None:
        # 한 글자도 받습니다. 마스터에 물·꿀·무·배 같은 한 음절 재료가 41종 있습니다.
        value = value.strip()
        if value and value not in seen:
            seen.append(value)

    add(name)
    without_suffix = _PREP_SUFFIX.sub("", name).strip()
    add(without_suffix)
    for base in (name, without_suffix):
        add(_PREP_PREFIX.sub("", base))
    for base in list(seen):
        if base.endswith("오일"):
            add(base[:-2] + "유")
    return seen


def _unit(quantity: str) -> str | None:
    """`75g` 에서 단위만. 숫자를 떼고 남는 것이 단위입니다."""
    unit = re.sub(r"[\d./⅓⅔¼½¾\s]", "", quantity or "")
    return unit[:30] or None


# 유니코드 분수 기호. 원천이 `½큰술` 처럼 쓰는 일이 있습니다.
_VULGAR_FRACTIONS = {
    "⅓": Fraction(1, 3),
    "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4),
    "½": Fraction(1, 2),
    "¾": Fraction(3, 4),
}

# **순서가 중요합니다.** 대분수 -> 분수 -> 소수 순으로 시도합니다.
# 소수를 먼저 보면 `1/2` 의 `1` 만 먹고 분수를 놓칩니다(실제로 그렇게 틀렸습니다).
_MIXED_NUMBER = re.compile(r"^(\d+)\s+(\d+)\s*/\s*(\d+)")
_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)")
_PLAIN_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)")


def _quantity(raw: str) -> Decimal | None:
    """수량 표기에서 숫자만. **분수를 분수로 계산합니다.**

    예전에는 `re.sub(r"[^\\d.]", "", raw)` 로 숫자가 아닌 글자를 전부 지웠습니다.
    그러면 `1/2알` 에서 `/` 까지 사라져 `"12"` 가 남고, 수량이 **12** 로 저장됐습니다.
    COOKRCP01 5,813개 수량 표기 중 2건이 실제로 그렇게 들어갔습니다.

    계산이 안 되면 **`None` 을 돌려줍니다.** 원문은 `raw_text` 에 그대로 남으므로
    틀린 숫자를 남기는 것보다 비우는 편이 낫습니다(`모르는 값은 지어내지 않는다`).
    """
    text = (raw or "").strip()
    if not text:
        return None

    total = Fraction(0)
    found = False
    for symbol, value in _VULGAR_FRACTIONS.items():
        if symbol in text:
            total += value
            text = text.replace(symbol, " ")
            found = True

    text = text.lstrip()
    if match := _MIXED_NUMBER.match(text):
        whole, numerator, denominator = (int(value) for value in match.groups())
        if denominator == 0:
            return None
        total += whole + Fraction(numerator, denominator)
        found = True
    elif match := _FRACTION.match(text):
        numerator, denominator = (int(value) for value in match.groups())
        if denominator == 0:
            return None
        total += Fraction(numerator, denominator)
        found = True
    elif match := _PLAIN_NUMBER.match(text):
        total += Fraction(match.group(1))
        found = True

    if not found:
        return None
    return Decimal(str(round(float(total), 2)))


def _decimal(value: Any) -> Decimal | None:
    """NUMERIC 컬럼용. 숫자가 아니면 None."""
    text = _text(value)
    if not text:
        return None
    try:
        return Decimal(str(round(float(text), 2)))
    except (ValueError, InvalidOperation):
        return None


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


async def run_recipe_load(rows: RecipeRows, *, settings: Settings | None = None) -> LoadReport:
    """staging 에 COPY 하고 recipe / recipe_ingredient / recipe_step 에 반영합니다.

    `run_load` 와 같은 SQL(`002`/`003`/`005`)을 씁니다. 다른 점은 입력이 2·3단계
    산출물이 아니라 구조화된 parquet 이라는 것뿐입니다. `004`(보관기준)는 이 데이터에
    해당 내용이 없어 건너뜁니다.

    한 트랜잭션입니다. `003` 에서 실패하면 `002` 가 넣은 레시피도 남지 않습니다.
    """
    settings = settings or get_settings()
    report = LoadReport(skipped_recipes=rows.skipped)

    async with load_connection_scope(settings) as conn:
        await run_sql_file(conn, settings.sql_dir / "001_staging_tables.sql")
        report.applied_sql.append("001_staging_tables.sql")

        # 레시피 계열만 비웁니다. 보관기준 staging 은 다른 작업이 쓰고 있을 수 있습니다.
        await conn.execute(
            "TRUNCATE staging_recipe, staging_recipe_step, staging_recipe_ingredient, staging_ingredient_match"
        )

        plan = (
            ("staging_recipe", STAGING_RECIPE_COLUMNS, rows.recipes),
            ("staging_recipe_step", STAGING_RECIPE_STEP_COLUMNS, rows.steps),
            ("staging_recipe_ingredient", STAGING_RECIPE_INGREDIENT_COLUMNS, rows.ingredients),
            ("staging_ingredient_match", STAGING_MATCH_COLUMNS, rows.matches),
        )
        for table, columns, records in plan:
            for start in range(0, len(records), settings.copy_chunk_size):
                await conn.copy_records_to_table(
                    table,
                    records=records[start : start + settings.copy_chunk_size],
                    columns=list(columns),
                )
            report.staged[table] = len(records)

        for name in ("002_insert_recipe.sql", "003_insert_recipe_ingredient.sql", "005_insert_recipe_step.sql"):
            await run_sql_file(conn, settings.sql_dir / name)
            report.applied_sql.append(name)

        for table in ("recipe", "recipe_ingredient", "recipe_step"):
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            report.row_counts[table] = int(count or 0)

    return report
