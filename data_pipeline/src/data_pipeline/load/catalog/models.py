"""staging 행 묶음과 컬럼 순서.

`CATEGORY_COLUMNS` 등은 `sql/001_staging_tables.sql` 의 컬럼 순서와 같아야 합니다.
COPY 는 이름이 아니라 **순서**로 넣기 때문에, 어긋나면 에러 없이 엉뚱한 칸에 들어갑니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORY_COLUMNS = ("path", "parent_path", "category_type", "name", "depth", "metadata")
PRODUCT_COLUMNS = (
    "source_type",
    "source_product_id",
    "name",
    "price",
    "product_type",
    "category_path",
    "brand_name",
    "storage_type",
    "origin_country",
    "weight_g",
    "unit_count",
    "sku",
    "stock_quantity",
    "metadata",
)
PRODUCT_INGREDIENT_COLUMNS = (
    "source_type",
    "source_product_id",
    "normalized_name",
    "ingredient_id",
    "role",
    "quantity_g",
    "ratio",
)


@dataclass(slots=True)
class CatalogRows:
    """COPY 로 밀어넣을 카탈로그 행 묶음."""

    categories: list[tuple[Any, ...]] = field(default_factory=list)
    products: list[tuple[Any, ...]] = field(default_factory=list)
    product_ingredients: list[tuple[Any, ...]] = field(default_factory=list)
    skipped_products: int = 0
    # 셋 중 어디에도 안 맞아 비운 보관 유형. 원본에 새 표기가 들어오면 여기 쌓입니다.
    unknown_storage_types: dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """적재할 것이 하나도 없는지."""
        return not (self.categories or self.products)
