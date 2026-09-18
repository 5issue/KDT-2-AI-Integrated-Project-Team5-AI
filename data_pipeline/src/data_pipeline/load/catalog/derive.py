"""상품명과 카테고리로 구성 재료를 **유추**합니다.

나머지 모듈과 성격이 다릅니다. `rows` 와 `normalize` 는 raw 에 있는 것을 옮기지만,
여기는 **없는 것을 추측합니다.** raw 에 `ingredients` 가 없는 상품을 그냥 두면
Product -> Ingredient -> Recipe 루프가 거기서 끊기기 때문입니다.

틀릴 수 있는 로직이라 따로 두고 따로 잽니다. 이미 들어온 구성 재료는 건드리지 않고,
한 글자 재료(`물`)가 아무 상품명에나 걸리지 않도록 최소 길이를 둡니다.
"""

from __future__ import annotations

from data_pipeline.domain import ingredient_match_key
from data_pipeline.load.catalog.models import PRODUCT_COLUMNS, CatalogRows

# 상품명 부분일치에 쓸 최소 길이. `물` 처럼 한 글자인 재료는 아무 상품명에나 걸립니다.
MIN_SUBSTRING_LENGTH = 2


def derive_product_ingredients(rows: CatalogRows, lookup: dict[str, int]) -> int:
    """상품명과 카테고리로 구성 재료를 유추합니다.

    raw 의 `ingredients` 가 비어 있어 `product_ingredient` 를 못 만들면
    Product -> Ingredient -> Recipe -> 부족재료 -> Product 루프가 끊깁니다.
    실제로 2,553건 전부 비어 있었습니다.

    두 신호를 씁니다. 실측 커버리지는 88% 입니다.
    1. 상품명에 들어 있는 마스터 재료명 중 **가장 긴 것** (87%)
       `[피쉬쉘] 자숙 칵테일 새우살 200g` -> `새우`
    2. 없으면 카테고리 잎 이름의 정확 일치 (29%)
       `수산 > 해산물/조개류 > 새우` -> `새우`

    한 상품에 재료 하나만 답니다. 밀키트나 양념육은 여러 재료로 이루어지지만,
    상품명만으로는 구성 비율을 알 수 없어 억지로 늘리면 노이즈가 됩니다.
    구성 재료가 실제로 필요한 상품은 raw 의 `ingredients` 로 받는 편이 낫습니다.

    이미 `ingredients` 로 들어온 상품은 건드리지 않습니다.
    """
    if not lookup:
        return 0

    keys = sorted((key for key in lookup if len(key) >= MIN_SUBSTRING_LENGTH), key=len, reverse=True)
    already = {(row[0], row[1]) for row in rows.product_ingredients}
    source_index = PRODUCT_COLUMNS.index("source_type")
    id_index = PRODUCT_COLUMNS.index("source_product_id")
    name_index = PRODUCT_COLUMNS.index("name")
    path_index = PRODUCT_COLUMNS.index("category_path")

    derived = 0
    for row in rows.products:
        identity = (row[source_index], row[id_index])
        if identity in already:
            continue
        haystack = ingredient_match_key(str(row[name_index]))
        match = next((key for key in keys if key in haystack), None)
        if match is None:
            # 카테고리 잎도 정확 일치부터 보고, 없으면 부분일치를 봅니다.
            # `파스타면` 안에 `파스타` 가 들어 있는 식입니다.
            leaf = ingredient_match_key(str(row[path_index] or "").split(" > ")[-1])
            if leaf in lookup:
                match = leaf
            elif leaf:
                match = next((key for key in keys if key in leaf), None)
        if match is None:
            continue
        rows.product_ingredients.append((*identity, match, lookup[match], "PRIMARY", None, None))
        derived += 1
    return derived
