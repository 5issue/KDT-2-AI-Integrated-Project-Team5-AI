"""KIPIL의 Storage Guideline을 규칙으로 선별해 대상 DB에 적재합니다.

KIPIL의 935행에는 서비스 자연키 `(ingredient_id, storage_location, storage_context)`가
중복되는 조합이 160개 있습니다. 전수 분해해 보면 그중 125개는 **서로 다른 FoodKeeper 원천이
한 Ingredient에 뭉친 것**이고, 같은 원천이 기간만 다르게 들어온 경우는 0건입니다.

따라서 이 스크립트는 중복을 병합하지 않습니다. 기간이 같은 조합만 대표 1건으로 접고,
기간이 다른 조합은 적재하지 않고 보류 리포트로 남깁니다. 가장 짧은 기간을 고르는 식으로
자동 확정하면 생닭 상품에 튀긴 닭 지침이 사실처럼 표시됩니다. 모르는 값은 지어내지 않습니다.

보류는 버리는 것이 아니라 Ingredient 매핑 과제 목록입니다. 원천을 부위·형태별 child
Ingredient로 가르는 기준은 `docs/product-ingredient-storage-normalization-guide.md` 3.3, 5.6절입니다.

기본 동작은 대상 DB를 바꾸지 않는 dry-run이며, Production 적용에는 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

import psycopg

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_CONFIRMATION = "LOAD_STORAGE_GUIDELINE_V1"
Target = Literal["local", "production"]

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


@dataclass(frozen=True, slots=True)
class HeldGroup:
    """적재하지 않고 검토 대상으로 남긴 자연키 조합입니다."""

    ingredient_id: int
    storage_location: str
    storage_context: str
    row_count: int
    sources: list[str]
    durations: list[str]
    reason: str


def load_env(path: Path) -> dict[str, str]:
    """비밀 값을 출력하지 않고 단순 env 파일을 읽습니다."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_url(values: dict[str, str], key: str) -> str:
    """환경 변수 또는 env 파일에서 접속 문자열을 읽습니다. 값 자체는 출력하지 않습니다."""
    value = os.environ.get(key) or values.get(key)
    if not value:
        raise ValueError(f"{key}가 설정되지 않았습니다. 접속 문자열 값은 출력하지 않습니다.")
    return value


def validate_confirmation(target: Target, apply: bool, confirmation: str | None) -> None:
    """Production 에 실제로 쓰기 전 확인 문자열을 요구합니다."""
    if target == "production" and apply and confirmation != PRODUCTION_CONFIRMATION:
        raise ValueError(f"Production 적용에는 --confirm-production {PRODUCTION_CONFIRMATION} 이 필요합니다.")


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


def select_representatives(rows: list[Guideline]) -> tuple[list[Guideline], list[HeldGroup]]:
    """자연키별로 적재할 대표 1건과 보류할 조합을 가릅니다.

    같은 자연키에 여러 행이 있어도 기간이 하나면 표기만 다른 것이므로 대표 1건을 고릅니다.
    기간이 둘 이상이면 서로 다른 원천이 뭉친 것이므로 적재하지 않습니다. 대표는 원천 식별자
    순서로 고정해, 몇 번을 돌려도 같은 행이 뽑히게 합니다.
    """
    groups: dict[tuple[int, str, str], list[Guideline]] = defaultdict(list)
    for row in rows:
        groups[row.key].append(row)

    accepted: list[Guideline] = []
    held: list[HeldGroup] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda row: row.origin)
        durations = {row.duration for row in members}
        if len(durations) == 1:
            accepted.append(members[0])
            continue
        ingredient_id, location, context = key
        held.append(
            HeldGroup(
                ingredient_id=ingredient_id,
                storage_location=location,
                storage_context=context,
                row_count=len(members),
                sources=sorted({row.source_food_name for row in members}),
                durations=sorted({row.duration_text for row in members}),
                reason="같은 자연키에 기간이 다른 원천이 둘 이상입니다. 자동 병합하지 않습니다.",
            )
        )
    return accepted, held


def fetch_source(url: str) -> list[Guideline]:
    """원천 DB에서 보관 지침 전체를 읽습니다."""
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(SOURCE_SQL)
        return [Guideline(*row) for row in cursor.fetchall()]


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


def write_rows(url: str, rows: list[Guideline]) -> tuple[int, int]:
    """대표 행을 한 트랜잭션에서 upsert 하고 신규·갱신 건수를 돌려줍니다."""
    inserted = 0
    updated = 0
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
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
    return inserted, updated


def write_report(path: Path, held: list[HeldGroup]) -> None:
    """보류 조합을 JSONL 로 남깁니다. 이 파일이 Ingredient 매핑 과제 목록입니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for group in sorted(held, key=lambda g: (g.ingredient_id, g.storage_location, g.storage_context)):
            handle.write(json.dumps(asdict(group), ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    """명령행 인자를 읽습니다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--source-url-env", default="DATABASE_URL_KIPIL")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "data/audits/storage_guideline_holds.jsonl")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    """선별 결과를 보고하고, --apply 일 때만 대상 DB에 적재합니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production)
    values = load_env(args.env_file)
    source_url = env_url(values, args.source_url_env)
    target_url = env_url(values, args.target_url_env)
    if source_url == target_url:
        raise ValueError("source와 target DB가 같습니다.")

    source_rows = fetch_source(source_url)
    accepted, held = select_representatives(source_rows)
    validate_enums(accepted)
    absent = missing_ingredients(target_url, accepted)
    if absent:
        raise ValueError(f"대상 DB에 없는 ingredient_id 가 {len(absent)}건 있습니다: {absent[:10]}")

    held_rows = sum(group.row_count for group in held)
    write_report(args.report, held)
    print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
    print(f"source_rows={len(source_rows)}")
    print(f"accepted_rows={len(accepted)} accepted_ingredients={len({row.ingredient_id for row in accepted})}")
    print(f"held_groups={len(held)} held_rows={held_rows} held_ingredients={len({g.ingredient_id for g in held})}")
    print(f"report={args.report}")
    if not args.apply:
        return
    inserted, updated = write_rows(target_url, accepted)
    print(f"inserted={inserted} updated={updated}")


if __name__ == "__main__":
    main()
