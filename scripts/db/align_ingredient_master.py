"""Production 재료 마스터를 기준 로더(`load_ingredient_master.py`) 결과에 맞춥니다.

`config/ingredient_master_alignment.csv` 한 파일이 할 일 전체입니다.

- `INSERT`: 기준에는 있는데 대상에 없는 재료를 추가합니다. 부모는 식별키로 찾습니다.
- `RENAME`: 같은 재료인데 이름이 기준과 다른 행의 이름을 바꿉니다. 식별키는 그대로 둡니다.

별칭은 `reset_ingredient_aliases.py` 가 이 파일까지 검토된 목록으로 읽어 맞춥니다. 여기서는
새로 넣는 행에만 별칭을 씁니다. 몇 번을 돌려도 같은 결과가 나오며, 기본은 dry-run 이고
Production 적용에는 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import cast

import psycopg

from scripts.db._env import Target, env_url, load_env, validate_confirmation

ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_CSV = ROOT / "config" / "ingredient_master_alignment.csv"
PRODUCTION_CONFIRMATION = "ALIGN_INGREDIENT_MASTER_V1"


def read_actions(path: Path = ALIGNMENT_CSV) -> list[dict[str, str]]:
    """정렬 설정을 읽고 동작 이름을 확인합니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    unknown = sorted({row["action"] for row in rows} - {"INSERT", "RENAME"})
    if unknown:
        raise ValueError(f"알 수 없는 action 입니다: {unknown}")
    return rows


def plan(actions: list[dict[str, str]], existing: dict[str, tuple[int, str]]) -> list[dict[str, str]]:
    """아직 적용되지 않은 동작만 남깁니다. `existing` 은 식별키 -> (id, 이름) 입니다."""
    todo = []
    for row in actions:
        current = existing.get(row["source_identity_key"])
        if row["action"] == "INSERT" and current is None:
            parent = row["parent_source_identity_key"]
            if parent and parent not in existing:
                raise ValueError(f"부모가 대상 DB에 없습니다: {parent} ({row['name']})")
            todo.append(row)
        elif row["action"] == "RENAME":
            if current is None:
                raise ValueError(f"이름을 바꿀 재료가 없습니다: {row['source_identity_key']}")
            if current[1] != row["name"]:
                todo.append(row)
    return todo


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
    """적용할 동작을 보고하고, --apply 일 때만 한 트랜잭션으로 씁니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production, token=PRODUCTION_CONFIRMATION)
    url = env_url(load_env(args.env_file), args.target_url_env)

    actions = read_actions()
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_identity_key, ingredient_id, name FROM ingredient")
            existing = {key: (ingredient_id, name) for key, ingredient_id, name in cursor.fetchall()}
        todo = plan(actions, existing)
        print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
        print(f"configured={len(actions)} to_apply={len(todo)}")
        for row in todo:
            print(f"  {row['action']} {row['name']} ({row['source_identity_key']})")
        if not args.apply:
            return
        with connection.cursor() as cursor:
            for row in todo:
                if row["action"] == "RENAME":
                    cursor.execute(
                        "UPDATE ingredient SET name = %s, normalized_name = %s WHERE source_identity_key = %s",
                        (row["name"], row["name"], row["source_identity_key"]),
                    )
                    continue
                parent = row["parent_source_identity_key"]
                cursor.execute(
                    "INSERT INTO ingredient (name, normalized_name, is_raw_material, aliases, parent_ingredient_id, "
                    "source_identity_key, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        row["name"],
                        row["name"],
                        row["is_raw_material"] == "true",
                        [alias for alias in row["aliases"].split("|") if alias],
                        existing[parent][0] if parent else None,
                        row["source_identity_key"],
                        json.dumps({"source": "ingredient_master_alignment.csv"}, ensure_ascii=False),
                    ),
                )
        connection.commit()


if __name__ == "__main__":
    main()
