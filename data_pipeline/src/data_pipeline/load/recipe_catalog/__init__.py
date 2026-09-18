"""식약처 조리식품 레시피 DB(COOKRCP01) 적재.

`data/raw/cookrcp01_all.parquet` 1,156건입니다. 기존 한국어 레시피 30건의 38배라,
"앞으로 한국어 raw 만 적재" 방침의 실제 재료가 됩니다.

## LLM 을 쓰지 않습니다

`product_raw.parquet` 과 같은 이유입니다. 이 데이터는 **이미 구조화되어** 들어옵니다.
컬럼 의미를 판단할 필요가 없으니 1~3단계를 태울 이유가 없습니다.

## 세 조각

    parsing   재료 문자열 -> (이름, 수량).  순수 함수. DB/파일 안 봄
    rows      raw 레코드 -> staging 행 튜플. DB 안 봄
    loader    COPY + INSERT.                 여기만 DB 에 닿음

한 파일 508줄이던 것을 나눈 것입니다. 기존 import 경로(`from data_pipeline.load
import recipe_catalog` 뒤 `recipe_catalog.<이름>`)를 그대로 쓸 수 있게 여기서 다시
내보냅니다. 테스트가 `_quantity` 처럼 비공개 이름도 쓰고 있어 함께 내보냅니다.
"""

from data_pipeline.load.recipe_catalog.loader import run_load_recipes, run_recipe_load
from data_pipeline.load.recipe_catalog.parsing import (
    _quantity,
    clean_step,
    match_candidates,
    parse_ingredients,
)
from data_pipeline.load.recipe_catalog.rows import (
    MAX_STEPS,
    REQUIRED_COLUMNS,
    SOURCE_TYPE,
    RecipeRows,
    build_recipe_rows,
    write_records,
)

__all__ = [
    "MAX_STEPS",
    "REQUIRED_COLUMNS",
    "SOURCE_TYPE",
    "RecipeRows",
    "_quantity",
    "build_recipe_rows",
    "clean_step",
    "match_candidates",
    "parse_ingredients",
    "run_load_recipes",
    "run_recipe_load",
    "write_records",
]
