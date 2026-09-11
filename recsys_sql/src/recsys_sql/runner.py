"""카탈로그 쿼리 실행과 실행계획 점검."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from recsys_sql.catalog import CatalogError, SqlQuery
from recsys_sql.config import Settings, get_settings

_PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "int": (int,),
    "float": (int, float),
    "str": (str,),
    "bool": (bool,),
    "date": (str,),
    "list[int]": (list, tuple),
    "list[str]": (list, tuple),
}


def validate_params(query: SqlQuery, params: dict[str, Any]) -> dict[str, Any]:
    """선언된 파라미터가 다 왔는지, 타입이 맞는지 확인합니다."""
    if missing := sorted(set(query.params) - set(params)):
        raise CatalogError(f"{query.name}: 파라미터가 빠졌습니다: {missing}")
    if extra := sorted(set(params) - set(query.params)):
        raise CatalogError(f"{query.name}: 선언되지 않은 파라미터입니다: {extra}")

    for name, type_name in query.params.items():
        value = params[name]
        expected = _PYTHON_TYPES[type_name]
        # bool 은 int 의 서브클래스라 int 자리에 들어가는 것을 따로 막습니다.
        if type_name == "int" and isinstance(value, bool):
            raise CatalogError(f"{query.name}.{name}: int 자리에 bool 이 들어왔습니다.")
        if not isinstance(value, expected):
            raise CatalogError(f"{query.name}.{name}: {type_name} 을 기대했지만 {type(value).__name__} 입니다.")
    return params


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
