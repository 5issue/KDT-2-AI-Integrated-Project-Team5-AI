"""KIPIL의 Storage Guideline을 규칙으로 선별해 대상 DB에 적재합니다.

KIPIL의 935행에는 서비스 자연키 `(ingredient_id, storage_location, storage_context)`가
중복되는 조합이 160개 있습니다. 전수 분해해 보면 그중 125개는 **서로 다른 FoodKeeper 원천이
한 Ingredient에 뭉친 것**이고, 같은 원천이 기간만 다르게 들어온 경우는 0건입니다.

뭉친 원천은 세 단계로 풉니다.

1. 원재료(`is_raw_material`) Ingredient 에서는 가공·조리 원천을 뺍니다. 생닭에 너겟이나
   튀긴 닭 지침을 붙이지 않습니다. 가공·조리 식품의 지침은 그 제품 기준이라 생고기 상품에
   보여 주면 틀린 보관 정보가 됩니다. 예를 들어 생닭 상품의 `냉장·해동후` 에 치킨너겟 기준
   1-2일이 나가고 있었습니다. 가공 원천 목록은 `config/foodkeeper_processed_sources.csv`
   (FoodKeeper 카테고리 11-14, 16, 17)입니다. 햄·베이컨처럼 원래 가공품인 Ingredient 는 그대로 둡니다.
2. `config/foodkeeper_ingredient_child_rules.csv` 가 가리키는 원천은 부위 child Ingredient 로
   옮깁니다. `Pork, loin chops` 는 `돼지고기` 가 아니라 `돼지고기 > 등심` 의 지침입니다.
   규칙은 원천 항목 단위로 검토한 것이라 원래 Ingredient 보다 우선합니다. `Beef, short ribs` 가
   `돼지고기` 에 잘못 붙어 있어도 `소고기 > 갈비` 로 옮깁니다.
3. 그래도 한 자연키에 기간이 다른 원천이 남으면 **가장 짧은 기간**을 대표로 고릅니다.
   긴 쪽을 보여 주면 상한 음식을 먹으라고 하는 셈이기 때문입니다. 고른 조합은 모두
   결정 리포트로 남겨 검토할 수 있게 합니다.

기간이 같은 조합은 표기만 다른 것이므로 원천 식별자 순서로 대표 1건을 고릅니다.
원천을 부위·형태별 child 로 가르는 기준은 `docs/product-ingredient-storage-normalization-guide.md`
3.3, 5.6절입니다. 적재는 upsert 로 하며, 대상에서 지우는 행은 1단계 규칙에 걸리는 행(원재료에
붙은 가공 원천)뿐입니다.

기본 동작은 대상 DB를 바꾸지 않는 dry-run이며, Production 적용에는 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import psycopg

from scripts.db._env import Target, env_url, load_env, validate_confirmation

ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_CONFIRMATION = "LOAD_STORAGE_GUIDELINE_V1"
CHILD_RULES_CSV = ROOT / "config" / "foodkeeper_ingredient_child_rules.csv"
PROCESSED_SOURCES_CSV = ROOT / "config" / "foodkeeper_processed_sources.csv"

# 기간을 일 단위로 맞춰 짧은 쪽을 고릅니다. recsys_sql 의 product_storage_guideline 과 같은 환산입니다.
UNIT_DAYS = {"시간": Decimal(1) / 24, "일": Decimal(1), "주": Decimal(7), "개월": Decimal(30), "년": Decimal(365)}

# 0013_storage_bootstrap 의 CHECK 와 같은 값입니다. 여기서 먼저 걸러야 트랜잭션 중간이 아니라
# 적재 전에 무엇이 틀렸는지 알 수 있습니다.
LOCATIONS = ("냉장", "냉동", "상온")
CONTEXTS = ("일반", "구매후", "개봉후", "해동후")
DURATION_UNITS = ("시간", "일", "주", "개월", "년")

# source_item_id 는 옮기지 않습니다. staging 에서만 원천 row 를 식별하는 값이고
# 0012 가 서비스 테이블에서 뺀 컬럼입니다.
SOURCE_SQL = """
SELECT ingredient_id,
       storage_location,
       storage_context,
       source_food_name,
       source_food_subtitle,
       source_slot,
       duration_min,
       duration_max,
       duration_unit,
       duration_text,
       storage_tips
FROM storage_guideline
"""

INSERT_SQL = """
INSERT INTO storage_guideline (
    ingredient_id, source_food_name, source_food_subtitle, source_slot,
    storage_location, storage_context,
    duration_min, duration_max, duration_unit, duration_text, storage_tips
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ingredient_id, storage_location, storage_context) DO UPDATE SET
    source_food_name = EXCLUDED.source_food_name,
    source_food_subtitle = EXCLUDED.source_food_subtitle,
    source_slot = EXCLUDED.source_slot,
    duration_min = EXCLUDED.duration_min,
    duration_max = EXCLUDED.duration_max,
    duration_unit = EXCLUDED.duration_unit,
    duration_text = EXCLUDED.duration_text,
    storage_tips = EXCLUDED.storage_tips,
    updated_at = now()
RETURNING (xmax = 0) AS inserted
"""


@dataclass(frozen=True, slots=True)
class Guideline:
    """서비스 테이블에 그대로 들어가는 보관 지침 한 줄입니다."""

    ingredient_id: int
    storage_location: str
    storage_context: str
    source_food_name: str
    source_food_subtitle: str | None
    source_slot: str
    duration_min: Decimal | None
    duration_max: Decimal | None
    duration_unit: str | None
    duration_text: str
    storage_tips: str | None

    @property
    def key(self) -> tuple[int, str, str]:
        """서비스 조회 자연키입니다."""
        return (self.ingredient_id, self.storage_location, self.storage_context)

    @property
    def duration(self) -> tuple[Decimal | None, Decimal | None, str | None]:
        """기간이 같은지 비교할 때 쓰는 값입니다. 표기(`duration_text`)는 보지 않습니다."""
        return (self.duration_min, self.duration_max, self.duration_unit)

    @property
    def origin(self) -> tuple[str, str, str]:
        """원천 식별자입니다. 대표를 고르는 순서를 고정하는 데 씁니다."""
        return (self.source_food_name, self.source_food_subtitle or "", self.source_slot)

    @property
    def days(self) -> Decimal | None:
        """상한 기간을 일 단위로 바꿉니다. 기간이 없으면 None 입니다."""
        value = self.duration_max if self.duration_max is not None else self.duration_min
        if value is None or self.duration_unit is None:
            return None
        return value * UNIT_DAYS[self.duration_unit]


@dataclass(frozen=True, slots=True)
class Decision:
    """기간이 다른 원천이 뭉친 자연키에서 무엇을 골랐는지 남기는 기록입니다."""

    ingredient_id: int
    storage_location: str
    storage_context: str
    row_count: int
    chosen: str
    chosen_duration: str
    others: list[str]


def read_child_rules(path: Path = CHILD_RULES_CSV) -> dict[tuple[str, str], str]:
    """(원천 식품명, 부제) -> child 식별키입니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["source_food_name"], row["source_food_subtitle"]): row["target_source_identity_key"]
            for row in csv.DictReader(handle)
        }


def read_processed_sources(path: Path = PROCESSED_SOURCES_CSV) -> set[tuple[str, str]]:
    """가공·조리 카테고리의 (원천 식품명, 부제) 입니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        return {(row["source_food_name"], row["source_food_subtitle"]) for row in csv.DictReader(handle)}


def drop_processed_from_raw(
    rows: list[Guideline], processed: set[tuple[str, str]], raw_ingredients: set[int]
) -> tuple[list[Guideline], int]:
    """원재료 Ingredient 에 붙은 가공·조리 원천을 뺍니다."""
    kept = [
        row
        for row in rows
        if row.ingredient_id not in raw_ingredients
        or (row.source_food_name, row.source_food_subtitle or "") not in processed
    ]
    return kept, len(rows) - len(kept)


def apply_child_rules(
    rows: list[Guideline], rules: dict[tuple[str, str], str], children: dict[str, int]
) -> tuple[list[Guideline], int]:
    """규칙이 가리키는 원천을 child Ingredient 로 옮깁니다.

    `children` 은 대상 DB 의 child 식별키 -> ingredient_id 입니다. 규칙은 원천 항목 단위로
    검토한 것이므로 원래 붙어 있던 Ingredient 가 무엇이든 규칙을 따릅니다.
    """
    moved = 0
    result = []
    for row in rows:
        key = rules.get((row.source_food_name, row.source_food_subtitle or ""))
        child = children.get(key) if key else None
        if child is not None and child != row.ingredient_id:
            row = replace(row, ingredient_id=child)
            moved += 1
        result.append(row)
    return result, moved


def validate_enums(rows: list[Guideline]) -> None:
    """0013 의 CHECK 와 같은 허용값인지 적재 전에 확인합니다."""
    for row in rows:
        where = f"(ingredient {row.ingredient_id})"
        if row.storage_location not in LOCATIONS:
            raise ValueError(f"허용되지 않은 storage_location 입니다: {row.storage_location!r} {where}")
        if row.storage_context not in CONTEXTS:
            raise ValueError(f"허용되지 않은 storage_context 입니다: {row.storage_context!r} {where}")
        if row.duration_unit is not None and row.duration_unit not in DURATION_UNITS:
            raise ValueError(f"허용되지 않은 duration_unit 입니다: {row.duration_unit!r} {where}")


def select_representatives(rows: list[Guideline]) -> tuple[list[Guideline], list[Decision]]:
    """자연키별로 적재할 대표 1건을 고릅니다.

    기간이 하나면 표기만 다른 것이므로 원천 식별자 순서의 첫 행을 씁니다. 기간이 둘 이상이면
    가장 짧은 기간을 고르고, 기간이 없는 행은 뒤로 보냅니다. 같은 길이면 원천 식별자 순서로
    고정해 몇 번을 돌려도 같은 행이 뽑히게 합니다.
    """
    groups: dict[tuple[int, str, str], list[Guideline]] = defaultdict(list)
    for row in rows:
        groups[row.key].append(row)

    accepted: list[Guideline] = []
    decisions: list[Decision] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda row: row.origin)
        if len({row.duration for row in members}) == 1:
            accepted.append(members[0])
            continue
        chosen = min(members, key=lambda row: (row.days is None, row.days or 0, row.origin))
        accepted.append(chosen)
        ingredient_id, location, context = key
        decisions.append(
            Decision(
                ingredient_id=ingredient_id,
                storage_location=location,
                storage_context=context,
                row_count=len(members),
                chosen=" / ".join(filter(None, chosen.origin[:2])),
                chosen_duration=chosen.duration_text,
                others=sorted(
                    {
                        f"{row.source_food_name} {row.source_food_subtitle or ''}".strip() + f": {row.duration_text}"
                        for row in members
                        if row is not chosen
                    }
                ),
            )
        )
    return accepted, decisions


def fetch_source(url: str) -> list[Guideline]:
    """원천 DB에서 보관 지침 전체를 읽습니다."""
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(SOURCE_SQL)
        return [Guideline(*row) for row in cursor.fetchall()]


def fetch_target_ingredients(url: str, keys: list[str]) -> tuple[dict[str, int], set[int]]:
    """대상 DB 에서 child 식별키 -> ingredient_id 와 원재료 Ingredient id 집합을 읽습니다."""
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT source_identity_key, ingredient_id FROM ingredient WHERE source_identity_key = ANY(%s)", (keys,)
        )
        children = dict(cursor.fetchall())
        cursor.execute("SELECT ingredient_id FROM ingredient WHERE is_raw_material")
        raw = {row[0] for row in cursor.fetchall()}
    return children, raw


def missing_ingredients(url: str, rows: list[Guideline]) -> list[int]:
    """대상 DB에 없는 ingredient_id 를 찾습니다. FK 위반을 트랜잭션 전에 잡습니다."""
    wanted = sorted({row.ingredient_id for row in rows})
    if not wanted:
        return []
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.storage_guideline') IS NOT NULL")
        table_row = cursor.fetchone()
        if table_row is None or not table_row[0]:
            raise ValueError("대상 DB에 storage_guideline 이 없습니다. migration 을 먼저 적용하세요.")
        cursor.execute("SELECT unnest(%s::bigint[]) EXCEPT SELECT ingredient_id FROM ingredient", (wanted,))
        return sorted(row[0] for row in cursor.fetchall())


PRUNE_PROCESSED_SQL = """
DELETE FROM storage_guideline sg
USING ingredient i
WHERE i.ingredient_id = sg.ingredient_id
  AND i.is_raw_material
  AND (sg.source_food_name, COALESCE(sg.source_food_subtitle, '')) IN (
      SELECT * FROM unnest(%s::text[], %s::text[])
  )
"""


def write_rows(url: str, rows: list[Guideline], processed: set[tuple[str, str]]) -> tuple[int, int, int]:
    """대표 행을 한 트랜잭션에서 upsert 하고 신규·갱신·삭제 건수를 돌려줍니다.

    upsert 는 행을 지우지 않으므로, 예전 규칙으로 원재료에 붙은 가공 원천 행(생닭의 너겟 지침)은
    남습니다. 적재에서 빼는 규칙과 같은 규칙으로 대상에서도 지웁니다. 그 밖의 행은 지우지 않습니다.
    """
    inserted = 0
    updated = 0
    names, subtitles = zip(*sorted(processed)) if processed else ((), ())
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(PRUNE_PROCESSED_SQL, (list(names), list(subtitles)))
            pruned = cursor.rowcount
            for row in rows:
                cursor.execute(
                    INSERT_SQL,
                    (
                        row.ingredient_id,
                        row.source_food_name,
                        row.source_food_subtitle,
                        row.source_slot,
                        row.storage_location,
                        row.storage_context,
                        row.duration_min,
                        row.duration_max,
                        row.duration_unit,
                        row.duration_text,
                        row.storage_tips,
                    ),
                )
                result = cursor.fetchone()
                if result is not None and result[0]:
                    inserted += 1
                else:
                    updated += 1
        connection.commit()
    return inserted, updated, pruned


def write_report(path: Path, decisions: list[Decision]) -> None:
    """짧은 기간을 고른 조합을 JSONL 로 남깁니다. 검토와 child 매핑 과제 목록입니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    """명령행 인자를 읽습니다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--source-url-env", default="DATABASE_URL_KIPIL")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "data/audits/storage_guideline_decisions.jsonl")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    """선별 결과를 보고하고, --apply 일 때만 대상 DB에 적재합니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production, token=PRODUCTION_CONFIRMATION)
    values = load_env(args.env_file)
    source_url = env_url(values, args.source_url_env)
    target_url = env_url(values, args.target_url_env)
    if source_url == target_url:
        raise ValueError("source와 target DB가 같습니다.")

    source_rows = fetch_source(source_url)
    rules = read_child_rules()
    children, raw = fetch_target_ingredients(target_url, sorted(set(rules.values())))
    processed = read_processed_sources()
    rows, dropped = drop_processed_from_raw(source_rows, processed, raw)
    rows, moved = apply_child_rules(rows, rules, children)
    accepted, decisions = select_representatives(rows)
    validate_enums(accepted)
    absent = missing_ingredients(target_url, accepted)
    if absent:
        raise ValueError(f"대상 DB에 없는 ingredient_id 가 {len(absent)}건 있습니다: {absent[:10]}")

    write_report(args.report, decisions)
    print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
    print(f"source_rows={len(source_rows)} processed_dropped={dropped} moved_to_child={moved}")
    print(f"accepted_rows={len(accepted)} accepted_ingredients={len({row.ingredient_id for row in accepted})}")
    print(f"shortest_picked_groups={len(decisions)}")
    print(f"report={args.report}")
    if not args.apply:
        return
    inserted, updated, pruned = write_rows(target_url, accepted, processed)
    print(f"inserted={inserted} updated={updated} pruned_processed={pruned}")


if __name__ == "__main__":
    main()
