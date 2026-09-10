# _template

복사해서 시작하는 예시 쿼리 3종입니다. 이 폴더는 그대로 두고, 본인 폴더에 복사해서 고치세요.

| 파일 | 추천 시나리오 |
| --- | --- |
| `fridge_recipe_match.sql` | 냉장고에 있는 재료로 지금 만들 수 있는 레시피 |
| `missing_ingredient_products.sql` | 그 레시피에 부족한 재료를 채울 상품 |
| `reorder_candidates.sql` | 다 떨어졌을 때가 된 재구매 후보 |

세 쿼리 모두 **실제 Neon 스키마** 컬럼명을 씁니다. `ai_context/database_schema.md` 와
다른 지점이 있으니 주의하세요.

- `recipe_ingredient` 의 재료 FK 는 `ingredient_id` 입니다 (문서의 `ingredient_id2` 아님)
- `recipe_product` 의 PK 는 `(recipe_id, ingredient_id, product_id)` 입니다
- `product.stock_quantity` 는 NULL 허용이라 `COALESCE(..., 0) > 0` 로 비교합니다.
  `NULL > 0` 은 NULL 이라 조건이 조용히 거짓이 됩니다.
