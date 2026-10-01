"""BE product-service 동기화용 상품 DML 내보내기.

## 왜 있나

AI 응답의 `product_id` 는 AI DB(kitchen) 기준입니다. BE 가 이 DML 을 그대로 적재해 **product_id 와
price 를 같은 값으로** 맞추므로(`docs/api/be-sync.md` 1절), 매핑 컬럼(external_product_id) 없이 id 를
그대로 주고받습니다. 재고(`stock_quantity`)는 초기값만 같고 주문이 일어나면 갈라지며, 화면 표시는
BE 값을 씁니다.

## 무엇을 내나

`category` 와 `product` 두 표를 **표준 SQL INSERT** 로 냅니다. BE 의 DB 종류를 모르므로
PostgreSQL 전용 문법(`::jsonb`, `E''`, `ON CONFLICT`)을 쓰지 않습니다. BE 가 자기 스키마에 맞춰
컬럼을 옮겨 적재합니다. 그래서 `metadata`, `embedding`, 시각 컬럼은 빼고, 크롤 원천 URL 만
`source_url` 로 꺼냅니다.

## 원천 두 가지

- **DB**(기본): 설정의 `DATABASE_URL` 에서 읽습니다.
- **pg_dump 파일**(`--from-dump`): 팀장이 BE 에 넘긴 `production_backup.sql` 같은 plain 덤프의
  `COPY public.category` / `COPY public.product` 블록을 읽습니다. DB 마다 채워진 컬럼이 다를 수 있어
  (예: `image_url`) BE 에 주는 정본은 production 백업에서 만듭니다. 두 원천을 대조한 기록은
  `docs/api/be-sync.md` 1절에 있습니다.

`product` 는 `category_id` 로 `category` 를 참조하므로 category 문장이 먼저 나옵니다.
문자열은 작은따옴표를 두 번 써서 이스케이프하고, 그 밖의 제어 문자는 원천에 없는 것을 검증합니다
(있으면 실패시켜 조용히 깨진 파일을 넘기지 않습니다).
"""

from __future__ import annotations

import json
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


COPY_PREFIX = "COPY public."
COPY_END = "\\."
# COPY text 형식의 이스케이프(백슬래시 다음 글자). 한 번만 훑어 해석합니다. 두 번 치환하면
# JSON 안의 `\\n` 이 복원된 뒤 다시 개행으로 해석되어 metadata 파싱이 깨집니다(CodeRabbit PR #45).
_COPY_ESCAPES = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}


def _copy_field(raw: str) -> str | None:
    if raw == "\\N":
        return None
    if "\\" not in raw:
        return raw
    decoded: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw) and raw[index + 1] in _COPY_ESCAPES:
            decoded.append(_COPY_ESCAPES[raw[index + 1]])
            index += 2
        else:
            decoded.append(char)
            index += 1
    return "".join(decoded)


def read_copy_block(path: Path, table: str) -> tuple[list[str], list[dict[str, str | None]]]:
    """plain pg_dump 에서 `COPY public.<table> (...) FROM stdin;` 블록 하나를 읽습니다. 값은 전부 문자열입니다.

    블록은 `\\.` 행으로 끝나야 합니다. 그 표시 없이 파일이 끝나면 잘린 덤프이므로 일부 행만
    정본으로 넘기지 않도록 실패시킵니다.
    """
    prefix = f"{COPY_PREFIX}{table} ("
    columns: list[str] = []
    rows: list[dict[str, str | None]] = []
    inside = False
    terminated = False
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not inside:
                if line.startswith(prefix):
                    columns = line[len(prefix) : line.index(")")].split(", ")
                    inside = True
                continue
            if line.startswith(COPY_END):
                terminated = True
                break
            rows.append(dict(zip(columns, (_copy_field(v) for v in line.rstrip("\n").split("\t")), strict=True)))
    if not columns:
        raise ValueError(f"덤프에 {table} 의 COPY 블록이 없습니다: {path}")
    if not terminated:
        raise ValueError(f"덤프의 {table} COPY 블록이 종료 표시 없이 끝났습니다(잘린 파일): {path}")
    return columns, rows


def _int(value: str | None) -> int | None:
    return None if value is None else int(value)


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def parse_dump(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """덤프에서 (categories, products, source_label) 을 DB 조회 결과와 같은 모양으로 만듭니다."""
    _, raw_categories = read_copy_block(path, "category")
    _, raw_products = read_copy_block(path, "product")
    categories: list[dict[str, Any]] = [
        {
            "category_id": _int(r["category_id"]),
            "category_type": r["category_type"],
            "parent_id": _int(r["parent_id"]),
            "name": r["name"],
            "depth": _int(r["depth"]),
        }
        for r in raw_categories
    ]
    products: list[dict[str, Any]] = []
    for r in raw_products:
        metadata = json.loads(r["metadata"] or "{}")
        products.append(
            {
                "product_id": _int(r["product_id"]),
                "sku": r["sku"],
                "name": r["name"],
                "category_id": _int(r["category_id"]),
                "product_type": r["product_type"],
                "storage_type": r["storage_type"],
                "origin_country": r["origin_country"],
                "weight_g": _decimal(r["weight_g"]),
                "unit_count": _int(r["unit_count"]),
                "price": _decimal(r["price"]),
                "stock_quantity": _int(r["stock_quantity"]),
                "is_active": r["is_active"] == "t",
                "brand_name": r["brand_name"],
                "image_url": r["image_url"],
                "source_url": metadata.get("source_url"),
            }
        )
    categories.sort(key=lambda c: c["category_id"])
    products.sort(key=lambda p: p["product_id"])
    sources = sorted({r["source_type"] for r in raw_products if r["source_type"]})
    return categories, products, f"{path.name} ({','.join(sources) or 'unknown'})"


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
    """읽기 전용 raw asyncpg 커넥션. 적재와 달리 direct 엔드포인트가 필요 없어 DATABASE_URL 을 씁니다.

    category 와 product 를 같은 스냅샷에서 읽도록 REPEATABLE READ 읽기 전용 트랜잭션으로 묶습니다.
    READ COMMITTED 로 두 번 읽으면 그 사이 커밋된 상품이 없는 카테고리를 가리킬 수 있습니다.
    """
    async with engine_scope(direct=False, settings=settings) as engine:
        async with engine.connect() as sa_conn:
            raw = await sa_conn.get_raw_connection()
            conn: asyncpg.Connection = raw.driver_connection  # type: ignore[assignment]
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                yield conn


async def run_export_products(
    *, settings: Settings | None = None, out_path: Path | None = None, dump_path: Path | None = None
) -> ExportReport:
    """category/product 를 읽어 DML 파일을 씁니다. `dump_path` 가 있으면 DB 대신 pg_dump 파일을 읽습니다.

    DB 는 읽기만 하고 바꾸지 않습니다.
    """
    out_path = out_path or DEFAULT_OUT_PATH
    if dump_path is not None:
        categories, products, source_label = parse_dump(dump_path)
    else:
        settings = settings or get_settings()
        async with read_connection(settings) as conn:
            categories = await fetch_rows(conn, CATEGORY_SQL)
            products = await fetch_rows(conn, PRODUCT_SQL)
            source = await conn.fetchval("SELECT string_agg(DISTINCT source_type, ',') FROM product")
        source_label = f"DB ({source or 'unknown'})"

    text = render_dml(categories, products, generated_at=datetime.now(UTC), source_label=source_label)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return ExportReport(
        path=out_path,
        categories=len(categories),
        products=len(products),
        bytes_written=len(text.encode("utf-8")),
    )
