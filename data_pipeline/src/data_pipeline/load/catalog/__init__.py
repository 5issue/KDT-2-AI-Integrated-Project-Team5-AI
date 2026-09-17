"""상품 카탈로그(category / product / product_ingredient) 적재.

레시피·보관기준과 달리 이 데이터는 **LLM 단계를 거치지 않습니다.** raw 가 이미 타깃
테이블 모양으로 정리되어 들어오기 때문입니다(`AI_CODING_CONTEXT/2026-09-10-1140:
raw-data-spec-for-category-product.md` 의 규격대로 팀이 만들어 줍니다). 컬럼 의미를
판단할 필요가 없으니 1~3단계를 태울 이유가 없고, 비용도 들지 않습니다.

`parent_id` 와 `category_id` 는 raw 에 넣을 수 없는 값이라 SQL 쪽에서 해석합니다.
파이썬은 `parent_path` / `category_path` 를 경로 문자열 그대로 넘깁니다.

## 다섯 조각

    models      CatalogRows + 컬럼 순서 (001_staging_tables.sql 과 같아야 함)
    normalize   raw 값 -> DB 타입. 순수 함수
    rows        raw 데이터셋 -> staging 행 튜플
    derive      상품명으로 구성 재료 유추          <- 여기만 "추측"
    loader      COPY + INSERT                      <- 여기만 DB

한 파일 495줄이던 것을 나눈 것입니다. 기존 import 경로(`from data_pipeline.load
import catalog` 뒤 `catalog.<이름>`)를 그대로 쓸 수 있게 여기서 다시 내보냅니다.
"""

from data_pipeline.load.catalog.derive import MIN_SUBSTRING_LENGTH, derive_product_ingredients
from data_pipeline.load.catalog.loader import run_load_catalog
from data_pipeline.load.catalog.models import (
    CATEGORY_COLUMNS,
    PRODUCT_COLUMNS,
    PRODUCT_INGREDIENT_COLUMNS,
    CatalogRows,
)
from data_pipeline.load.catalog.rows import (
    CATEGORY_REQUIRED,
    PRODUCT_REQUIRED,
    build_catalog_rows,
    render,
)

__all__ = [
    "CATEGORY_COLUMNS",
    "CATEGORY_REQUIRED",
    "MIN_SUBSTRING_LENGTH",
    "PRODUCT_COLUMNS",
    "PRODUCT_INGREDIENT_COLUMNS",
    "PRODUCT_REQUIRED",
    "CatalogRows",
    "build_catalog_rows",
    "derive_product_ingredients",
    "render",
    "run_load_catalog",
]
