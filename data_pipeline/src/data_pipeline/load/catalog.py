"""상품 카탈로그(category / product / product_ingredient) 적재.

레시피·보관기준과 달리 이 데이터는 **LLM 단계를 거치지 않습니다.** raw 가 이미 타깃
테이블 모양으로 정리되어 들어오기 때문입니다(`ai_context/AI_CODING_CONTEXT` 의
raw 데이터 요청 규격대로 팀이 만들어 줍니다). 컬럼 의미를 판단할 필요가 없으니
1~3단계를 태울 이유가 없고, 비용도 들지 않습니다.

여기서 하는 일은 세 가지뿐입니다.
- 문자열로 들어온 숫자를 DB 타입에 맞추기
- `parent_path` / `category_path` 를 경로 문자열 그대로 넘겨 SQL 이 부모를 찾게 하기
- 상품 구성 재료를 3단계 매칭 키로 정규화하기

`parent_id` 와 `category_id` 는 raw 에 넣을 수 없는 값이라 SQL 쪽에서 해석합니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from data_pipeline.batch.raw_source import RawDataset, iter_records
from data_pipeline.domain import ingredient_match_key

CATEGORY_COLUMNS = ("path", "parent_path", "category_type", "name", "depth", "metadata")
PRODUCT_COLUMNS = (
    "source_type",
    "source_product_id",
    "name",
    "price",
    "product_type",
    "category_path",
    "storage_type",
    "origin_country",
    "weight_g",
    "unit_count",
    "sku",
    "stock_quantity",
    "metadata",
)
PRODUCT_INGREDIENT_COLUMNS = ("source_type", "source_product_id", "normalized_name", "role", "quantity_g", "ratio")

# 이 컬럼들이 다 있어야 해당 데이터셋으로 봅니다.
#
# 상품은 원본 크롤(`kurly_products_food`)과 타깃 모양으로 정리한 것(`product_raw`)이
# 함께 들어옵니다. 둘 다 source_type/source_product_id/name/price 를 갖고 있어서
# 느슨하게 잡으면 같은 상품이 두 번 적재됩니다(실제로 2,553 -> 5,106 이 나왔습니다).
# `product_type` 과 `category_path` 는 정리본에만 있어 이것으로 가립니다.
CATEGORY_REQUIRED = ("category_type", "name", "parent_path")
PRODUCT_REQUIRED = ("source_type", "source_product_id", "name", "price", "product_type", "category_path")

# raw 가 문자열로 실어 보내는 빈 값들.
NULLISH = frozenset({"", "none", "null", "nan", "-"})


@dataclass(slots=True)
class CatalogRows:
    """COPY 로 밀어넣을 카탈로그 행 묶음."""

    categories: list[tuple[Any, ...]] = field(default_factory=list)
    products: list[tuple[Any, ...]] = field(default_factory=list)
    product_ingredients: list[tuple[Any, ...]] = field(default_factory=list)
    skipped_products: int = 0

    def is_empty(self) -> bool:
        """적재할 것이 하나도 없는지."""
        return not (self.categories or self.products)


def _text(value: Any) -> str | None:
    """빈 값 표기를 전부 None 으로 모읍니다. parquet 이 'None' 문자열로 싣고 옵니다."""
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in NULLISH else text


def _number(value: Any, places: int) -> Decimal | None:
    """NUMERIC 컬럼용. 콤마나 통화 기호가 섞여 와도 숫자만 추립니다."""
    text = _text(value)
    if text is None:
        return None
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    try:
        return Decimal(str(round(float(cleaned), places)))
    except (ValueError, InvalidOperation):
        return None


def _integer(value: Any) -> int | None:
    """INTEGER 컬럼용."""
    number = _number(value, 0)
    return int(number) if number is not None else None


def _finite(value: Any) -> Any:
    """NaN / Infinity 를 None 으로 바꿉니다.

    파이썬 json 은 NaN 을 그대로 뱉지만 PostgreSQL JSON 은 거부합니다
    (`Token "NaN" is invalid`). 크롤 데이터의 빈 수치가 NaN 으로 들어와 실제로 밟았습니다.
    """
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value


def _jsonb(value: Any) -> str:
    """metadata 는 JSONB 라 문자열로 넘깁니다. 파싱이 안 되면 원문을 담아 둡니다."""
    text = _text(value)
    if text is None:
        return "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return json.dumps({"raw": text}, ensure_ascii=False)
    payload = _finite(parsed if isinstance(parsed, dict) else {"raw": parsed})
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


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
                _text(payload.get("storage_type")),
                _text(payload.get("origin_country")),
                _number(payload.get("weight_g"), 2),
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
    if rows.products and not rows.product_ingredients:
        lines.append(
            "\n주의: 구성 재료가 하나도 없습니다. product_ingredient 가 비면\n"
            "  Product -> Ingredient -> Recipe -> 부족재료 -> Product 루프가 끊깁니다."
        )
    return "\n".join(lines)
