"""BE product-service 동기화용 상품 DML 내보내기.

## 왜 있나

AI 응답의 `product_id` 는 AI DB(kitchen) 기준이라 BE 상품 id 와 달랐습니다. 2026-09-29 BE 답변:
AI 쪽 상품 데이터 DML SQL 을 주면 BE 가 그대로 적재해 **product_id 와 price 를 같은 값으로**
맞추겠다고 했습니다. 그러면 매핑 컬럼(external_product_id) 없이 id 를 그대로 주고받습니다.
재고(`stock_quantity`)는 초기값만 같고 주문이 일어나면 갈라집니다. 그건 프로젝트 기간 안에
맞추지 않기로 했고, 화면 표시는 BE 값을 씁니다.

## 무엇을 내나

`category` 와 `product` 두 표를 **표준 SQL INSERT** 로 냅니다. BE 의 DB 종류를 모르므로
PostgreSQL 전용 문법(`::jsonb`, `E''`, `ON CONFLICT`)을 쓰지 않습니다. BE 가 자기 스키마에 맞춰
컬럼을 옮겨 적재합니다. 그래서 `metadata`, `embedding`, 시각 컬럼은 빼고, 크롤 원천 URL 만
`source_url` 로 꺼냅니다.

`product` 는 `category_id` 로 `category` 를 참조하므로 category 문장이 먼저 나옵니다.
문자열은 작은따옴표를 두 번 써서 이스케이프하고, 그 밖의 제어 문자는 원천에 없는 것을 검증합니다
(있으면 실패시켜 조용히 깨진 파일을 넘기지 않습니다).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from data_pipeline.config import PACKAGE_DIR, Settings, get_settings
from data_pipeline.db import engine_scope

DEFAULT_OUT_PATH = PACKAGE_DIR / "sql" / "export" / "product_dml.sql"
ROWS_PER_STATEMENT = 200

CATEGORY_COLUMNS: tuple[str, ...] = ("category_id", "category_type", "parent_id", "name", "depth")
PRODUCT_COLUMNS: tuple[str, ...] = (
    "product_id",
    "sku",
    "name",
    "category_id",
    "product_type",
    "storage_type",
    "origin_country",
    "weight_g",
    "unit_count",
    "price",
    "stock_quantity",
    "is_active",
    "brand_name",
    "image_url",
    "source_url",
)

CATEGORY_SQL = """
SELECT category_id, category_type, parent_id, name, depth
FROM category
ORDER BY category_id
"""

PRODUCT_SQL = """
SELECT product_id, sku, name, category_id, product_type, storage_type, origin_country,
       weight_g, unit_count, price, stock_quantity, is_active, brand_name, image_url,
       metadata ->> 'source_url' AS source_url
FROM product
ORDER BY product_id
"""

# 참고용 DDL. BE 가 컬럼 의미를 볼 수 있게 파일 머리에 주석으로 싣습니다.
REFERENCE_DDL = """\
-- 참고: AI DB 의 컬럼 정의 (BE 스키마에 맞춰 옮겨 적재하세요)
--   category(category_id BIGINT PK, category_type VARCHAR(30), parent_id BIGINT NULL -> category,
--            name VARCHAR(100), depth INTEGER)
--   product(product_id BIGINT PK, sku VARCHAR(100) NULL UNIQUE, name VARCHAR(255),
--           category_id BIGINT NULL -> category, product_type VARCHAR(30) [RAW_MATERIAL|PROCESSED_FOOD|MEAL_KIT],
--           storage_type VARCHAR(20) NULL [냉장|냉동|상온], origin_country VARCHAR(100) NULL,
--           weight_g NUMERIC(10,2) NULL, unit_count INTEGER NULL, price NUMERIC(12,2) [원],
--           stock_quantity INTEGER NULL [NULL=수량 미상, 0=품절], is_active BOOLEAN,
--           brand_name VARCHAR(100) NULL, image_url TEXT NULL, source_url TEXT NULL [크롤 원천])
-- product_id 와 price 는 AI 응답(RECO-01, RECIPE-03 등)의 값과 같습니다. 그대로 쓰면 매핑이 필요 없습니다.
-- stock_quantity 는 초기값입니다. 주문이 일어나면 AI DB 와 갈라지며 화면 표시는 BE 값을 씁니다.
"""


class UnsafeValueError(ValueError):
    """SQL 리터럴로 안전하게 낼 수 없는 값. 조용히 깨진 파일을 넘기지 않으려고 실패시킵니다."""


def sql_literal(value: object) -> str:
    """값 하나를 표준 SQL 리터럴로 바꿉니다."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if any(ord(ch) < 32 for ch in value):
            raise UnsafeValueError(f"제어 문자가 든 문자열은 내보내지 않습니다: {value[:40]!r}")
        if "\\" in value:
            # 표준 SQL 은 백슬래시를 그대로 두지만 MySQL 기본 설정은 이스케이프로 읽습니다.
            # 두 쪽에서 같은 값이 되도록 원천에 없는 것을 확인합니다.
            raise UnsafeValueError(f"백슬래시가 든 문자열은 내보내지 않습니다: {value[:40]!r}")
        return "'" + value.replace("'", "''") + "'"
    raise UnsafeValueError(f"지원하지 않는 타입입니다: {type(value).__name__}")


def render_inserts(table: str, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> list[str]:
    """행 묶음을 다중 VALUES INSERT 문 목록으로 바꿉니다. 문장당 `ROWS_PER_STATEMENT` 행입니다."""
    statements: list[str] = []
    chunk: list[str] = []
    header = f"INSERT INTO {table} ({', '.join(columns)}) VALUES"

    def flush() -> None:
        if chunk:
            statements.append(header + "\n" + ",\n".join(chunk) + ";")
            chunk.clear()

    for row in rows:
        chunk.append("  (" + ", ".join(sql_literal(row[column]) for column in columns) + ")")
        if len(chunk) >= ROWS_PER_STATEMENT:
            flush()
    flush()
    return statements


def render_dml(
    categories: Sequence[dict[str, Any]],
    products: Sequence[dict[str, Any]],
    *,
    generated_at: datetime,
    source_label: str,
) -> str:
    """파일 전체 텍스트. 머리 주석 + category INSERT + product INSERT."""
    lines = [
        "-- 5issue AI 파트 상품 데이터 DML (BE product-service 동기화용)",
        f"-- 생성: {generated_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} / 원천: {source_label}",
        f"-- category {len(categories)}행, product {len(products)}행. 표준 SQL 이라 PostgreSQL/MySQL 어디서나 돕니다.",
        "-- 다시 만들기: uv run data-pipeline export-products (data_pipeline/load/product_export.py)",
        REFERENCE_DDL.rstrip(),
        "",
        "-- category (product.category_id 가 참조하므로 먼저)",
        *render_inserts("category", CATEGORY_COLUMNS, categories),
        "",
        "-- product",
        *render_inserts("product", PRODUCT_COLUMNS, products),
        "",
    ]
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ExportReport:
    """내보내기 결과. 경로와 행 수만 담습니다."""

    path: Path
    categories: int
    products: int
    bytes_written: int

    def render(self) -> str:
        return (
            f"{self.path}\n"
            f"  category {self.categories:,}행, product {self.products:,}행, {self.bytes_written / 1024:,.0f} KB"
        )


async def fetch_rows(conn: asyncpg.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(record) for record in await conn.fetch(sql)]


@asynccontextmanager
async def read_connection(settings: Settings) -> AsyncIterator[asyncpg.Connection]:
    """읽기 전용 raw asyncpg 커넥션. 적재와 달리 direct 엔드포인트가 필요 없어 DATABASE_URL 을 씁니다."""
    async with engine_scope(direct=False, settings=settings) as engine:
        async with engine.connect() as sa_conn:
            raw = await sa_conn.get_raw_connection()
            conn: asyncpg.Connection = raw.driver_connection  # type: ignore[assignment]
            yield conn


async def run_export_products(*, settings: Settings | None = None, out_path: Path | None = None) -> ExportReport:
    """DB 에서 category/product 를 읽어 DML 파일을 씁니다. 읽기만 하고 DB 는 바꾸지 않습니다."""
    settings = settings or get_settings()
    out_path = out_path or DEFAULT_OUT_PATH
    async with read_connection(settings) as conn:
        categories = await fetch_rows(conn, CATEGORY_SQL)
        products = await fetch_rows(conn, PRODUCT_SQL)
        source = await conn.fetchval("SELECT string_agg(DISTINCT source_type, ',') FROM product")

    text = render_dml(categories, products, generated_at=datetime.now(UTC), source_label=str(source or "unknown"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return ExportReport(
        path=out_path,
        categories=len(categories),
        products=len(products),
        bytes_written=len(text.encode("utf-8")),
    )
