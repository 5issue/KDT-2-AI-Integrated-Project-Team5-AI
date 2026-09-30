"""상품 DML 내보내기 검증. DB 를 쓰지 않습니다.

BE 가 그대로 적재하는 파일이라 리터럴 이스케이프가 틀리면 BE 쪽에서 깨집니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from data_pipeline.load.product_export import (
    CATEGORY_COLUMNS,
    PRODUCT_COLUMNS,
    ROWS_PER_STATEMENT,
    UnsafeValueError,
    parse_dump,
    read_copy_block,
    render_dml,
    render_inserts,
    sql_literal,
)

NOW = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)


def product(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "product_id": 749,
        "sku": "M00000053948",
        "name": "[롯데] 몽쉘 생크림케이크 408g",
        "category_id": 364,
        "product_type": "PROCESSED_FOOD",
        "storage_type": None,
        "origin_country": "상품설명/상세정보 참조",
        "weight_g": Decimal("408.00"),
        "unit_count": None,
        "price": Decimal("5040.00"),
        "stock_quantity": 359,
        "is_active": True,
        "brand_name": "롯데웰푸드",
        "image_url": None,
        "source_url": "https://www.kurly.com/goods/1000175876",
    }
    row.update(overrides)
    return row


def category(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"category_id": 221, "category_type": "FOOD", "parent_id": None, "name": "양념육", "depth": 0}
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (42, "42"),
        (Decimal("5040.00"), "5040"),
        (Decimal("408.50"), "408.5"),
        (Decimal("0.00"), "0"),
        ("김치", "'김치'"),
        ("D'Amico", "'D''Amico'"),
    ],
)
def test_sql_literal(value: object, expected: str) -> None:
    assert sql_literal(value) == expected


@pytest.mark.parametrize("value", ["a\nb", "a\\b", "tab\there", object()])
def test_sql_literal_rejects_unsafe(value: object) -> None:
    with pytest.raises(UnsafeValueError):
        sql_literal(value)


def test_render_inserts_chunks_rows() -> None:
    rows = [product(product_id=1000 + index) for index in range(ROWS_PER_STATEMENT + 1)]
    statements = render_inserts("product", PRODUCT_COLUMNS, rows)
    assert len(statements) == 2
    assert statements[0].startswith("INSERT INTO product (product_id, sku, name, category_id")
    assert statements[0].count("\n  (") == ROWS_PER_STATEMENT
    assert statements[1].count("\n  (") == 1
    assert all(statement.endswith(");") for statement in statements)


def test_render_inserts_empty() -> None:
    assert render_inserts("category", CATEGORY_COLUMNS, []) == []


def test_render_dml_orders_category_before_product() -> None:
    text = render_dml([category()], [product()], generated_at=NOW, source_label="KURLY_CRAWL")
    assert "category 1행, product 1행" in text
    assert "2026-09-30T03:00:00Z" in text
    assert text.index("INSERT INTO category") < text.index("INSERT INTO product")
    assert "(221, 'FOOD', NULL, '양념육', 0)" in text
    assert (
        "(749, 'M00000053948', '[롯데] 몽쉘 생크림케이크 408g', 364, 'PROCESSED_FOOD', NULL, "
        "'상품설명/상세정보 참조', 408, NULL, 5040, 359, TRUE, '롯데웰푸드', NULL, "
        "'https://www.kurly.com/goods/1000175876')"
    ) in text
    # PostgreSQL 전용 문법이 섞이지 않았는지
    assert "::" not in text.replace("https://", "").replace("http://", "")
    assert "ON CONFLICT" not in text


def _copy(table: str, columns: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """COPY 블록 하나. 값은 탭으로 잇고 NULL 은 \\N 입니다."""
    body = "\n".join("\t".join(row) for row in rows)
    return f"COPY public.{table} ({', '.join(columns)}) FROM stdin;\n{body}\n\\.\n"


PRODUCT_DUMP_COLUMNS = [
    *"product_id sku name category_id product_type storage_type origin_country weight_g unit_count price".split(),
    *"stock_quantity is_active metadata embedding created_at updated_at source_type source_product_id".split(),
    "brand_name",
    "image_url",
]
DUMP = (
    "--\n-- PostgreSQL database dump\n--\n\n"
    + _copy(
        "category",
        "category_id category_type parent_id name depth metadata created_at".split(),
        [
            ["221", "FOOD", "\\N", "양념육", "0", "{}", "2026-09-10 07:18:20+00"],
            ["364", "FOOD", "221", "돼지고기", "1", "{}", "2026-09-10 07:18:20+00"],
        ],
    )
    + "\n"
    + _copy(
        "product",
        PRODUCT_DUMP_COLUMNS,
        [
            [
                "749",
                "M00000053948",
                "[롯데] 몽쉘 생크림케이크 408g",
                "364",
                "PROCESSED_FOOD",
                "\\N",
                "상품설명/상세정보 참조",
                "408.00",
                "\\N",
                "5040.00",
                "359",
                "t",
                '{"source_url": "https://www.kurly.com/goods/1000175876"}',
                "[0.1,0.2]",
                "2026-09-10 07:18:20+00",
                "2026-09-21 06:58:53+00",
                "KURLY_CRAWL",
                "1000175876",
                "롯데웰푸드",
                "https://img.example/749.jpg",
            ],
            [
                "750",
                "\\N",
                "D'Amico 소스",
                "\\N",
                "RAW_MATERIAL",
                "냉장",
                "\\N",
                "\\N",
                "1",
                "3000.00",
                "\\N",
                "f",
                "{}",
                "\\N",
                "2026-09-10 07:18:20+00",
                "2026-09-21 06:58:53+00",
                "KURLY_CRAWL",
                "2",
                "\\N",
                "\\N",
            ],
        ],
    )
)


def test_read_copy_block(tmp_path: Path) -> None:
    dump = tmp_path / "backup.sql"
    dump.write_text(DUMP, encoding="utf-8")
    columns, rows = read_copy_block(dump, "category")
    assert columns[:3] == ["category_id", "category_type", "parent_id"]
    assert rows[0]["parent_id"] is None
    assert rows[1]["name"] == "돼지고기"
    with pytest.raises(ValueError, match="COPY 블록이 없습니다"):
        read_copy_block(dump, "recipe")


def test_parse_dump_matches_db_row_shape(tmp_path: Path) -> None:
    dump = tmp_path / "backup.sql"
    dump.write_text(DUMP, encoding="utf-8")
    categories, products, label = parse_dump(dump)
    assert label == "backup.sql (KURLY_CRAWL)"
    assert categories[0] == category()
    assert products[0] == product(image_url="https://img.example/749.jpg")
    assert products[1]["is_active"] is False
    assert products[1]["source_url"] is None
    assert products[1]["price"] == Decimal("3000.00")
    text = render_dml(categories, products, generated_at=NOW, source_label=label)
    assert "'D''Amico 소스'" in text
    assert "'https://img.example/749.jpg'" in text
