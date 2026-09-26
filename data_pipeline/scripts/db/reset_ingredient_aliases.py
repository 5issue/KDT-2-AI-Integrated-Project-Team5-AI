"""Ingredient 별칭을 검토된 목록만 남기고 비웁니다.

검토된 목록은 세 설정 파일입니다. `config/ingredient_master_aliases.csv` 는 원재료 별칭(계란,
쇠고기)이고, `config/ingredient_master_processed_exceptions.csv` 의 `aliases` 열은 가공 예외
재료의 별칭(케첩, 마요)이며, `config/ingredient_master_alignment.csv` 는 마스터 정렬에서 넣거나
이름을 바꾼 재료의 별칭(맛술, 조미김)입니다. 가공 예외는 MFDS 대표식품 행만 `K-FIND-P:<대분류>:<대표코드>:<대표명>`
식별키로 맞춥니다. 중분류 예외와 INTERNAL 예외는 식별키 체계가 달라 여기서 다루지 않습니다.

`sync-master` 가 공공 영양성분 데이터의 중·소·세분류명(`생것`, `말린것`, 품종명, `감자`(전분의
원료) 등)을 별칭으로 넣었습니다. 이 값들은 동의어가 아니어서 상품·레시피 매칭에서 `감자 -> 전분`,
`고구마 -> 당면`, `백미 -> 찹쌀` 같은 오매칭을 만들었습니다.

별칭은 검토된 것만 씁니다. 지운 별칭은 리포트 CSV 로 남겨, 필요한 것은 검토 후 설정 파일에
올리게 합니다. 이미 만들어진 상품·레시피 매핑은 건드리지 않습니다. 기본은 dry-run 이며
Production 적용에는 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import cast

import psycopg

from scripts.db._env import Target, env_url, load_env, validate_confirmation

ROOT = Path(__file__).resolve().parents[3]
ALIASES_CSV = ROOT / "config" / "ingredient_master_aliases.csv"
PROCESSED_CSV = ROOT / "config" / "ingredient_master_processed_exceptions.csv"
ALIGNMENT_CSV = ROOT / "config" / "ingredient_master_alignment.csv"
PRODUCTION_CONFIRMATION = "RESET_INGREDIENT_ALIASES_V1"


def read_reviewed(
    aliases: Path = ALIASES_CSV, processed: Path = PROCESSED_CSV, alignment: Path = ALIGNMENT_CSV
) -> dict[str, list[str]]:
    """식별키 -> 검토된 별칭 목록입니다."""
    pairs: list[tuple[str, str]] = []
    with aliases.open(encoding="utf-8", newline="") as handle:
        pairs += [(row["source_identity_key"], row["alias"]) for row in csv.DictReader(handle)]
    with alignment.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pairs += [(row["source_identity_key"], alias) for alias in row["aliases"].split("|") if alias]
    with processed.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["source_dataset"] != "MFDS" or row["middle_category_code"]:
                continue
            code, name = row["representative_source_code"], row["representative_source_name"]
            key = f"K-FIND-P:{row['large_category_code']}:{code}:{name}"
            pairs += [(key, alias) for alias in row["aliases"].split("|") if alias]
    reviewed: dict[str, list[str]] = defaultdict(list)
    for key, alias in pairs:
        if alias not in reviewed[key]:
            reviewed[key].append(alias)
    return reviewed


def plan(
    rows: list[tuple[int, str, str, list[str]]], reviewed: dict[str, list[str]]
) -> list[tuple[int, str, list[str], list[str]]]:
    """별칭이 바뀔 (id, 이름, 새 별칭, 지울 별칭) 목록입니다."""
    changes = []
    for ingredient_id, name, key, aliases in rows:
        keep = sorted(reviewed.get(key, []))
        if sorted(aliases) != keep:
            changes.append((ingredient_id, name, keep, sorted(set(aliases) - set(keep))))
    return changes


def parse_args() -> argparse.Namespace:
    """명령행 인자를 읽습니다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "data/audits/ingredient_aliases_removed.csv")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    """바뀔 별칭을 리포트로 남기고, --apply 일 때만 한 트랜잭션으로 씁니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production, token=PRODUCTION_CONFIRMATION)
    url = env_url(load_env(args.env_file), args.target_url_env)

    reviewed = read_reviewed()
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ingredient_id, name, source_identity_key, aliases FROM ingredient ORDER BY 1")
            rows = cast(list[tuple[int, str, str, list[str]]], cursor.fetchall())
        changes = plan(rows, reviewed)

        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ingredient_id", "name", "removed_alias"])
            for ingredient_id, name, _, removed in changes:
                writer.writerows([ingredient_id, name, alias] for alias in removed)

        removed_total = sum(len(removed) for *_, removed in changes)
        print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
        print(f"ingredients_changed={len(changes)} aliases_removed={removed_total}")
        print(f"report={args.report}")
        if not args.apply:
            return
        with connection.cursor() as cursor:
            cursor.executemany(
                "UPDATE ingredient SET aliases = %s WHERE ingredient_id = %s",
                [(keep, ingredient_id) for ingredient_id, _, keep, _ in changes],
            )
        connection.commit()


if __name__ == "__main__":
    main()
