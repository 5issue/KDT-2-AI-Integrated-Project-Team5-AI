"""LLM 없이 되는 매칭. **마스터 조회와 정확 일치입니다.**

3단계는 두 단계인데 그 앞쪽입니다.

1. 2단계 산출물에서 매칭이 필요한 이름을 모으고(`collect_names`)
2. 마스터를 읽어(`fetch_master`) 그대로 맞는 것을 붙입니다(`exact_match`).

공짜라 먼저 씁니다. 여기서 안 붙은 것만 LLM 에게 갑니다(`requests` / `collect`).

`fetch_match_lookup` 은 3단계 전용이 아닙니다. `load-catalog` 와 `load-recipes` 가
상품명·레시피 재료를 마스터에 붙일 때 이 조회를 주입받아 씁니다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import text

from data_pipeline.config import Settings
from data_pipeline.db import engine_scope
from data_pipeline.domain import ingredient_match_key
from data_pipeline.stages.resolve.models import MatchResult, NameRequest


def collect_names(records_dir: Path) -> list[NameRequest]:
    """2단계 산출물 전체에서 매칭이 필요한 재료명을 모읍니다."""
    seen: dict[str, NameRequest] = {}

    def add(normalized: str, display: str, raw: str) -> None:
        key = ingredient_match_key(normalized or "")
        if not key:
            return
        if key in seen:
            seen[key].occurrence += 1
            return
        seen[key] = NameRequest(
            normalized_name=key,
            display_name=(display or key).strip(),
            sample_raw_text=(raw or display or key).strip(),
            occurrence=1,
        )

    for path in sorted(records_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            for item in payload.get("ingredients", []):
                add(item.get("normalized_name", ""), item.get("name", ""), item.get("raw_text", ""))
            if "normalized_name" in payload and "rules" in payload:
                add(
                    payload.get("normalized_name", ""),
                    payload.get("food_name_ko", ""),
                    payload.get("source_food_name", ""),
                )

    return sorted(seen.values(), key=lambda item: (-item.occurrence, item.normalized_name))


async def fetch_master(settings: Settings | None = None) -> list[dict[str, Any]]:
    """재료 마스터 전체를 읽습니다. 736행 정도라 프롬프트에 통째로 들어갑니다."""
    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT ingredient_id, name, normalized_name, aliases, parent_ingredient_id "
                        "FROM ingredient ORDER BY ingredient_id"
                    )
                )
            ).mappings()
            return [dict(row) for row in rows]


async def fetch_match_lookup(settings: Settings | None = None) -> dict[str, int]:
    """마스터 전체를 매칭 키 -> ingredient_id 로. 이름·기본형·별칭을 모두 담습니다.

    상품명에서 재료를 유추할 때 씁니다. 같은 키가 여럿이면 낮은 id 를 씁니다
    (상위 항목이 먼저 들어와 있어 더 일반적인 재료가 잡힙니다).
    """
    lookup: dict[str, int] = {}
    for row in await fetch_master(settings):
        for candidate in (row.get("normalized_name"), row.get("name"), *(row.get("aliases") or [])):
            key = ingredient_match_key(str(candidate or ""))
            if key:
                lookup.setdefault(key, int(row["ingredient_id"]))
    return lookup


async def exact_match(
    names: Sequence[NameRequest], settings: Settings | None = None
) -> tuple[list[MatchResult], list[NameRequest]]:
    """정확 일치로 붙는 것부터 처리합니다. 중복 이름은 상위 항목 > 낮은 id 순으로 고릅니다."""
    if not names:
        return [], []

    async with engine_scope(settings=settings) as engine:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "WITH wanted AS (SELECT UNNEST(CAST(:names AS text[])) AS normalized_name) "
                        "SELECT DISTINCT ON (w.normalized_name) "
                        "       w.normalized_name, i.ingredient_id, i.name, "
                        "       CASE WHEN LOWER(BTRIM(i.normalized_name)) = w.normalized_name THEN 0 "
                        "            WHEN LOWER(BTRIM(i.name)) = w.normalized_name THEN 1 ELSE 2 END AS rank "
                        "FROM wanted w JOIN ingredient i "
                        "  ON LOWER(REGEXP_REPLACE(i.normalized_name, '\\s', '', 'g')) = w.normalized_name "
                        "  OR LOWER(REGEXP_REPLACE(i.name, '\\s', '', 'g')) = w.normalized_name "
                        "  OR EXISTS (SELECT 1 FROM UNNEST(i.aliases) a "
                        "             WHERE LOWER(REGEXP_REPLACE(a, '\\s', '', 'g')) = w.normalized_name) "
                        "ORDER BY w.normalized_name, rank, (i.parent_ingredient_id IS NULL) DESC, i.ingredient_id"
                    ),
                    {"names": [item.normalized_name for item in names]},
                )
            ).mappings()
            hits = {row["normalized_name"]: row for row in rows}

    matched = [
        MatchResult(
            normalized_name=item.normalized_name,
            ingredient_id=int(hits[item.normalized_name]["ingredient_id"]),
            matched_name=str(hits[item.normalized_name]["name"]),
            method="exact",
            confidence=1.0,
        )
        for item in names
        if item.normalized_name in hits
    ]
    remaining = [item for item in names if item.normalized_name not in hits]
    return matched, remaining
