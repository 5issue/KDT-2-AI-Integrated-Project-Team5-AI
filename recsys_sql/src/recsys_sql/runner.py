"""카탈로그 쿼리 실행과 실행계획 점검.

`validate_params` 는 `catalog` 로 옮겼습니다. 서빙이 SQLAlchemy 없이 파라미터만
검증할 수 있어야 하기 때문입니다. 기존 import 경로를 위해 여기서 다시 내보냅니다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import SqlQuery, validate_params
from recsys_sql.config import Settings, get_settings

__all__ = ["ExplainReport", "QueryResult", "explain_query", "run_query", "validate_params"]


@dataclass(slots=True)
class QueryResult:
    """쿼리 실행 결과."""

    query_name: str
    rows: list[Row[Any]]
    elapsed_ms: float

    def as_dicts(self) -> list[dict[str, Any]]:
        """행을 dict 로 바꿉니다."""
        return [dict(row._mapping) for row in self.rows]


@dataclass(slots=True)
class ExplainReport:
    """실행계획 점검 결과."""

    query_name: str
    plan: dict[str, Any]
    seq_scans: list[str] = field(default_factory=list)
    total_cost: float = 0.0
    # 테이블별 Seq Scan 추정 행수 중 가장 큰 값.
    seq_scan_rows: dict[str, int] = field(default_factory=dict)

    def forbidden_seq_scans(self, forbidden: tuple[str, ...], *, row_limit: int = 0) -> list[str]:
        """풀스캔이 금지된 테이블 중 실제로 풀스캔이 걸린 것.

        `row_limit` 을 주면 그보다 작게 추정되는 스캔은 넘어갑니다. 작은 표에서는
        플래너가 인덱스보다 순차 읽기를 고르는 편이 맞고, 그걸 실패로 보면 SQL 을
        억지로 비틀게 됩니다. 자세한 이유는 `Settings.seq_scan_row_limit` 에 적었습니다.
        """
        return sorted(
            {table for table in self.seq_scans if table in forbidden and self.seq_scan_rows.get(table, 0) >= row_limit}
        )


async def run_query(
    conn: AsyncConnection,
    query: SqlQuery,
    params: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
) -> QueryResult:
    """쿼리를 실행합니다. 값은 항상 바인딩으로 넘어갑니다."""
    settings = settings or get_settings()
    bound = validate_params(query, params or {})

    timeout_ms = int(settings.query_timeout_seconds * 1000)
    await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))

    started = time.perf_counter()
    result = await conn.execute(text(query.sql), bound)
    rows = list(result.all())
    return QueryResult(query_name=query.name, rows=rows, elapsed_ms=(time.perf_counter() - started) * 1000)


def _collect_seq_scans(node: dict[str, Any], found: list[str], rows: dict[str, int]) -> None:
    """실행계획 트리에서 Seq Scan 대상 테이블과 추정 행수를 모읍니다."""
    if node.get("Node Type") == "Seq Scan" and "Relation Name" in node:
        table = str(node["Relation Name"])
        found.append(table)
        # 같은 표가 여러 번 나오면 가장 큰 스캔으로 봅니다.
        estimated = int(node.get("Plan Rows", 0))
        rows[table] = max(rows.get(table, 0), estimated)
    for child in node.get("Plans", []):
        _collect_seq_scans(child, found, rows)


async def explain_query(
    conn: AsyncConnection,
    query: SqlQuery,
    params: dict[str, Any] | None = None,
) -> ExplainReport:
    """EXPLAIN (FORMAT JSON) 으로 실행계획을 받아옵니다. ANALYZE 는 쓰지 않아 실제 실행은 하지 않습니다."""
    bound = validate_params(query, params or {})
    raw = (await conn.execute(text(f"EXPLAIN (FORMAT JSON) {query.sql}"), bound)).scalar_one()
    plan_list = json.loads(raw) if isinstance(raw, str) else raw
    plan = plan_list[0]["Plan"]

    seq_scans: list[str] = []
    seq_scan_rows: dict[str, int] = {}
    _collect_seq_scans(plan, seq_scans, seq_scan_rows)
    return ExplainReport(
        query_name=query.name,
        plan=plan,
        seq_scans=seq_scans,
        total_cost=float(plan.get("Total Cost", 0.0)),
        seq_scan_rows=seq_scan_rows,
    )
