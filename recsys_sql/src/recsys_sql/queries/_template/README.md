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
- `product.stock_quantity` 는 NULL 허용이고 **NULL 은 품절이 아니라 "수량을 모른다"** 입니다
  (팀 정규화 가이드 4.3). `IS NULL OR > 0` 으로 비교하세요.
  `COALESCE(..., 0) > 0` 은 수량 미상을 전부 품절 취급해 결과가 통째로 빕니다 -
  실제로 `reorder_candidates` 가 그것 때문에 항상 빈손이었습니다.
  `NULL > 0` 이 NULL 이라 조건이 조용히 거짓이 되는 것도 함께 주의하세요.
  (`seed-demo` 가 2,553행 전부에 숫자를 넣은 뒤로는 데모 DB 에 NULL 이 없지만,
  새로 적재한 상품은 다시 NULL 로 들어옵니다.)
