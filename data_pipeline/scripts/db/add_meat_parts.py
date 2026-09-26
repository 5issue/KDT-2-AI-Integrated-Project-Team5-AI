"""육류 부위를 대표 재료의 child Ingredient 로 추가합니다.

`config/ingredient_master_meat_parts.csv` 의 검토된 부위(돼지 8, 소 10, 닭 7, 양 4)를
`K-FIND:09:<대표코드>:<대표명>:SMALL:<부위>` 식별키로 만들고, 부모는 같은 대표코드의
REPRESENTATIVE 행(`K-FIND:09:<대표코드>:<대표명>`)으로 겁니다.

이름은 K-FIND 원천 표기(`목심(목심살)` 의 `목심`)를 그대로 씁니다. 이미 같은 식별키가 있으면
건드리지 않습니다. Production 의 돼지고기 `목심`(1029) 처럼 먼저 들어온 부위는 그대로 둡니다.
기본은 dry-run 이며 Production 적용에는 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import psycopg

from scripts.db._env import Target, env_url, load_env, validate_confirmation

ROOT = Path(__file__).resolve().parents[3]
PARTS_CSV = ROOT / "config" / "ingredient_master_meat_parts.csv"
PRODUCTION_CONFIRMATION = "ADD_MEAT_PARTS_V1"

INSERT_SQL = """
INSERT INTO ingredient (name, normalized_name, is_raw_material, parent_ingredient_id, source_identity_key, metadata)
VALUES (%s, %s, true, %s, %s, %s)
ON CONFLICT (source_identity_key) DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class Part:
    """부모 아래에 둘 부위 하나입니다."""

    name: str
    key: str
    parent_key: str
    source_small_codes: list[str]


def read_parts(path: Path = PARTS_CSV) -> list[Part]:
    """부위 설정을 읽어 식별키를 만듭니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    parts = []
    for row in rows:
        code, name = row["resolved_representative_code"], row["resolved_representative_name"]
        parent_key = f"K-FIND:{row['large_category_code']}:{code}:{name}"
        parts.append(
            Part(
                name=row["canonical_name"],
                key=f"{parent_key}:SMALL:{row['canonical_name']}",
                parent_key=parent_key,
                source_small_codes=row["source_small_codes"].split("|"),
            )
        )
    keys = [part.key for part in parts]
    if len(keys) != len(set(keys)):
        raise ValueError("부위 설정에 같은 식별키가 두 번 있습니다.")
    return parts


def plan(connection: psycopg.Connection, parts: list[Part]) -> tuple[list[Part], list[str], dict[str, int]]:
    """새로 넣을 부위, 부모가 없는 부위, 식별키 -> id 를 돌려줍니다."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT source_identity_key, ingredient_id FROM ingredient WHERE source_identity_key = ANY(%s)",
            ([part.key for part in parts] + [part.parent_key for part in parts],),
        )
        existing = dict(cursor.fetchall())
    orphans = sorted({part.parent_key for part in parts if part.parent_key not in existing})
    new = [part for part in parts if part.key not in existing and part.parent_key in existing]
    return new, orphans, existing


def parse_args() -> argparse.Namespace:
    """명령행 인자를 읽습니다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    """추가할 부위를 보고하고, --apply 일 때만 한 트랜잭션으로 넣습니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production, token=PRODUCTION_CONFIRMATION)
    url = env_url(load_env(args.env_file), args.target_url_env)

    parts = read_parts()
    with psycopg.connect(url) as connection:
        new, orphans, existing = plan(connection, parts)
        if orphans:
            raise ValueError(f"대상 DB에 부모 대표 재료가 없습니다: {orphans}")
        print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
        print(f"configured={len(parts)} already_present={len(parts) - len(new)} to_insert={len(new)}")
        for part in new:
            print(f"  + {part.parent_key.rsplit(':', 1)[-1]} > {part.name}")
        if not args.apply:
            return
        with connection.cursor() as cursor:
            for part in new:
                metadata = {"source": "ingredient_master_meat_parts.csv", "source_small_codes": part.source_small_codes}
                values = (part.name, part.name, existing[part.parent_key], part.key)
                cursor.execute(INSERT_SQL, (*values, json.dumps(metadata, ensure_ascii=False)))
        connection.commit()
    print(f"inserted={len(new)}")


if __name__ == "__main__":
    main()
