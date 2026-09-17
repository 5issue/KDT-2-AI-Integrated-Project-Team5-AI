"""raw 데이터셋 -> staging 행 튜플.

어느 데이터셋을 카테고리로 보고 어느 것을 상품으로 볼지는 **필수 컬럼**으로 가립니다
(아래 `CATEGORY_REQUIRED` / `PRODUCT_REQUIRED`). 느슨하게 잡으면 같은 상품이 두 번
들어옵니다 - 실제로 2,553 이 5,106 이 된 적이 있습니다.

값 변환은 `normalize` 가 하고, 여기서는 어떤 행을 만들지만 정합니다. DB 는 안 봅니다.
"""

from __future__ import annotations

import json
from typing import Any

from data_pipeline.batch.raw_source import RawDataset, iter_records
from data_pipeline.domain import ingredient_match_key
from data_pipeline.load.catalog.models import PRODUCT_COLUMNS, CatalogRows
from data_pipeline.load.catalog.normalize import (
    _brand_name,
    _integer,
    _jsonb,
    _number,
    _storage_type,
    _text,
    _weight_grams,
)

# 이 컬럼들이 다 있어야 해당 데이터셋으로 봅니다.
#
# 상품은 원본 크롤(`kurly_products_food`)과 타깃 모양으로 정리한 것(`product_raw`)이
# 함께 들어옵니다. 둘 다 source_type/source_product_id/name/price 를 갖고 있어서
# 느슨하게 잡으면 같은 상품이 두 번 적재됩니다(실제로 2,553 -> 5,106 이 나왔습니다).
# `product_type` 과 `category_path` 는 정리본에만 있어 이것으로 가립니다.
CATEGORY_REQUIRED = ("category_type", "name", "parent_path")
PRODUCT_REQUIRED = ("source_type", "source_product_id", "name", "price", "product_type", "category_path")


def _category_path(parent_path: str | None, name: str) -> str:
    """루트부터 자기까지의 경로. 부모를 찾는 키가 됩니다."""
    parent = (parent_path or "").strip()
    return f"{parent} > {name}" if parent else name


def build_catalog_rows(datasets: list[RawDataset]) -> CatalogRows:
    """카테고리/상품 데이터셋에서 staging 행을 만듭니다."""
    rows = CatalogRows()

    for dataset in datasets:
        columns = set(dataset.columns)
        if set(CATEGORY_REQUIRED) <= columns:
            _append_categories(rows, dataset)
        elif set(PRODUCT_REQUIRED) <= columns:
            _append_products(rows, dataset)

    # 같은 경로가 두 번 들어오면 staging PK 에서 터집니다. 먼저 온 것을 남깁니다.
    seen: set[Any] = set()
    rows.categories = [row for row in rows.categories if row[0] not in seen and not seen.add(row[0])]
    seen = set()
    rows.products = [row for row in rows.products if row[:2] not in seen and not seen.add(row[:2])]

    # 구성 재료도 같이 걷어내야 합니다. `_append_product_ingredients` 의 중복 제거는
    # 레코드 하나 안에서만 돕니다. 같은 상품이 raw 에 두 번 들어오면 상품은 위에서
    # 하나로 접히지만 구성 재료는 두 벌 남아, staging_product_ingredient 의
    # PK(source_type, source_product_id, normalized_name) 에서 COPY 가 터집니다.
    seen = set()
    rows.product_ingredients = [
        row for row in rows.product_ingredients if row[:3] not in seen and not seen.add(row[:3])
    ]

    rows.products = _dedupe_sku(rows.products)
    return rows


def _dedupe_sku(products: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """`sku` 는 UNIQUE 입니다. 원본에 중복이 있으면 뒤에 온 것의 sku 만 비웁니다.

    실제 데이터에서 sku 1,551개 중 103개가 중복이었습니다(중복분 109행).
    상품 자체는 자연키(source_type, source_product_id)로 구분되므로 행은 살리고
    sku 만 버립니다. 그대로 넣으면 uq_product_sku 에서 적재 전체가 롤백됩니다.
    """
    sku_index = PRODUCT_COLUMNS.index("sku")
    seen: set[str] = set()
    result: list[tuple[Any, ...]] = []
    for row in products:
        sku = row[sku_index]
        if sku and sku in seen:
            row = row[:sku_index] + (None,) + row[sku_index + 1 :]
        elif sku:
            seen.add(sku)
        result.append(row)
    return result


def _append_categories(rows: CatalogRows, dataset: RawDataset) -> None:
    """카테고리 한 데이터셋."""
    for record in iter_records(dataset):
        payload = record.payload
        name = _text(payload.get("name"))
        category_type = _text(payload.get("category_type"))
        if not name or not category_type:
            continue
        parent_path = _text(payload.get("parent_path")) or ""
        path = _category_path(parent_path, name)
        depth = _integer(payload.get("depth"))
        if depth is None:
            depth = len(path.split(" > ")) - 1

        metadata = json.loads(_jsonb(payload.get("metadata")))
        for key in ("source", "source_code", "is_edible"):
            value = payload.get(key)
            if value is not None and _text(value) is not None:
                metadata.setdefault(key, _text(value))

        rows.categories.append(
            (path, parent_path, category_type, name, depth, json.dumps(metadata, ensure_ascii=False))
        )


def _append_products(rows: CatalogRows, dataset: RawDataset) -> None:
    """상품 한 데이터셋. `ingredients` 가 있으면 구성 재료도 함께 만듭니다."""
    for record in iter_records(dataset):
        payload = record.payload
        source_type = _text(payload.get("source_type"))
        source_id = _text(payload.get("source_product_id"))
        name = _text(payload.get("name"))
        price = _number(payload.get("price"), 2)
        if not (source_type and source_id and name) or price is None:
            rows.skipped_products += 1
            continue

        rows.products.append(
            (
                source_type,
                source_id,
                name,
                price,
                _text(payload.get("product_type")) or "RAW_MATERIAL",
                _text(payload.get("category_path")),
                _brand_name(payload),
                _storage_type(payload.get("storage_type"), rows),
                _text(payload.get("origin_country")),
                _weight_grams(payload.get("weight_g"), name),
                _integer(payload.get("unit_count")),
                _text(payload.get("sku")),
                _integer(payload.get("stock_quantity")),
                _jsonb(payload.get("metadata")),
            )
        )
        _append_product_ingredients(rows, payload, source_type=source_type, source_id=source_id)


def _append_product_ingredients(
    rows: CatalogRows, payload: dict[str, Any], *, source_type: str, source_id: str
) -> None:
    """상품 구성 재료. `[{"name": "돼지고기", "role": "PRIMARY"}]` 형태를 받습니다."""
    raw = payload.get("ingredients")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return
    if not isinstance(raw, list):
        return

    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        key = ingredient_match_key(str(item.get("normalized_name") or item.get("name") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        rows.product_ingredients.append(
            (
                source_type,
                source_id,
                key,
                None,  # raw 로 들어온 것은 3단계 매칭 결과로 해석합니다
                _text(item.get("role")) or "PRIMARY",
                _number(item.get("quantity_g"), 2),
                _number(item.get("ratio"), 5),
            )
        )


def render(rows: CatalogRows) -> str:
    """사람이 읽을 요약."""
    lines = [
        f"카테고리        : {len(rows.categories)}행",
        f"상품            : {len(rows.products)}행",
        f"상품 구성 재료  : {len(rows.product_ingredients)}행",
    ]
    if rows.skipped_products:
        lines.append(f"필수값이 없어 뺀 상품: {rows.skipped_products}행")
    if rows.products:
        covered = len({(row[0], row[1]) for row in rows.product_ingredients})
        rate = covered / len(rows.products) * 100
        lines.append(f"  구성 재료가 붙은 상품: {covered}/{len(rows.products)} ({rate:.0f}%)")
        if not rows.product_ingredients:
            lines.append(
                "\n주의: 구성 재료가 하나도 없습니다. product_ingredient 가 비면\n"
                "  Product -> Ingredient -> Recipe -> 부족재료 -> Product 루프가 끊깁니다."
            )
    return "\n".join(lines)
