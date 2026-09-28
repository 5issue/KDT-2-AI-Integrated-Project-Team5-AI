"""서빙 경로에서 추천 이유 생성 서비스가 연결되는지 보여 주는 시연 스크립트.

서빙 코드를 고치지 않고, 서빙이 실제로 쓰는 세 조각(카탈로그 SQL 빌더, 응답 스키마, envelope)을
import 해 ``GET /recommendations/my-recipes?limit=3`` 한 요청을 그대로 재현합니다.

    SQL(my_recipe_candidates) -> MyRecipeItem.from_row -> generate_reasons_for_rows -> ApiResponse JSON

서빙 담당자가 라우터에 넣을 코드도 아래 ``build_response`` 와 같습니다. 연결 안내는
노션의 서빙 연결 가이드에 있습니다.

- DB 세션은 읽기 전용으로 엽니다. 사용자 ID 는 출력하지 않습니다.
- DB 는 서빙과 같은 설정(``serving/.env`` 의 ``DATABASE_URL``)으로 붙습니다.
  ``rag_lab/.env`` 에서는 OpenRouter 키만 읽습니다.
- ``--dry-run`` 이면 OpenRouter 를 부르지 않고 규칙 기반 문구만 씁니다.
- 실행: ``uv run python rag_lab/scripts/recommendation_reason_serving_demo.py [--dry-run] [--repeat N]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from rag_lab.reason_service import OpenRouterReasonClient, ReasonSettings, generate_reasons_for_rows
from rag_lab.reason_service.service import DEFAULT_TIMEOUT_SECONDS
from serving.db import create_pool
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import MyRecipeItem, MyRecipeListResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_reason_env() -> dict[str, str]:
    """rag_lab/.env 의 REASON_* / OPENROUTER_API_KEY 를 읽습니다. 값은 출력하지 않습니다.

    시연 편의로, OPENROUTER_API_KEY 가 없고 LLM_PROVIDER=openrouter 면 실험용 LLM_API_KEY 를 대신 씁니다.
    운영 코드(reason_service)는 이 대체를 모릅니다. 서빙은 OPENROUTER_API_KEY 를 씁니다.
    """
    env = dict(os.environ)
    path = PROJECT_ROOT / "rag_lab" / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip())
    if not env.get("OPENROUTER_API_KEY", "").strip() and env.get("LLM_PROVIDER", "").strip().lower() == "openrouter":
        env["OPENROUTER_API_KEY"] = env.get("LLM_API_KEY", "").strip()
    return env


async def load_ingredient_vocabulary(pool: Any) -> frozenset[str]:
    """환각 검사 사전. 서빙도 앱 시작 때 같은 쿼리로 한 번 읽어 두면 됩니다."""
    async with pool.acquire() as conn:
        await conn.execute("SET default_transaction_read_only = on")
        rows = await conn.fetch("SELECT name FROM ingredient")
    return frozenset(str(row["name"]) for row in rows)


async def build_response(
    pool: Any,
    user_id: int,
    limit: int,
    client: OpenRouterReasonClient | None,
    timeout_seconds: float,
    vocabulary: frozenset[str],
) -> tuple[str, list[str], dict[str, float]]:
    """서빙 라우터가 할 일 그대로. 반환값은 (응답 JSON, 카드별 출처, 구간별 ms)."""
    started = time.perf_counter()
    sql, args = build_query("my_recipe_candidates", {"user_id": user_id, "min_match_rate": 0.5, "max_results": limit})
    async with pool.acquire() as conn:
        await conn.execute("SET default_transaction_read_only = on")
        rows = [dict(row) for row in await conn.fetch(sql, *args)]
    after_sql = time.perf_counter()

    items = [MyRecipeItem.from_row(row) for row in rows]
    # 여기가 서빙에 추가될 부분입니다. 실패한 카드는 함수 안에서 규칙 기반 문구로 바뀌어 옵니다.
    results = await generate_reasons_for_rows(rows, client, timeout_seconds=timeout_seconds, vocabulary=vocabulary)
    for item, result in zip(items, results, strict=True):
        item.recommendation_reason = result.text
    after_reason = time.perf_counter()

    body = ApiResponse.success(MyRecipeListResponse(items=items)).model_dump_json()
    finished = time.perf_counter()
    timings = {
        "sql_ms": round((after_sql - started) * 1000, 1),
        "reason_ms": round((after_reason - after_sql) * 1000, 1),
        "total_ms": round((finished - started) * 1000, 1),
    }
    return body, [result.source for result in results], timings


async def main(dry_run: bool, repeat: int, limit: int) -> None:
    env = _load_reason_env()
    client: OpenRouterReasonClient | None = None
    settings: ReasonSettings | None = None
    if not dry_run:
        settings = ReasonSettings.from_env(env)
        print(f"model={settings.model} timeout={DEFAULT_TIMEOUT_SECONDS}s (OpenRouter)")
    else:
        print("dry-run: 규칙 기반 문구만 사용")

    pool = await create_pool()
    async with httpx.AsyncClient() as http:
        if settings is not None:
            client = OpenRouterReasonClient(http, settings)
        try:
            async with pool.acquire() as conn:
                await conn.execute("SET default_transaction_read_only = on")
                users = [
                    int(row["user_id"])
                    for row in await conn.fetch(
                        "SELECT user_id FROM user_fridge WHERE expires_at IS NULL OR expires_at >= NOW() "
                        "GROUP BY user_id ORDER BY user_id"
                    )
                ]
            if not users:
                print("냉장고가 있는 사용자가 없습니다.")
                return
            vocabulary = await load_ingredient_vocabulary(pool)
            print(f"환각 검사 사전: 재료 {len(vocabulary)}종")
            for index in range(repeat):
                user_id = users[index % len(users)]
                body, sources, timings = await build_response(
                    pool, user_id, limit, client, DEFAULT_TIMEOUT_SECONDS, vocabulary
                )
                data = json.loads(body)["data"]["items"]
                print(f"\n[{index + 1}/{repeat}] user_ref=user_{index % len(users) + 1:02d} {timings}")
                for item, source in zip(data, sources, strict=True):
                    print(f"  - {item['name']} ({source})")
                    print(f"    {item['recommendation_reason']}")
        finally:
            await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="OpenRouter 를 부르지 않고 규칙 기반 문구만 씁니다.")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=3)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.dry_run, arguments.repeat, arguments.limit))
