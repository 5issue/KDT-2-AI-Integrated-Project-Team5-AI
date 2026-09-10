-- staging_product_ingredient -> product_ingredient.
--
-- ingredient_id 는 3단계가 채운 staging_ingredient_match 에서 가져옵니다.
-- 매칭되지 않은 재료는 빠지고 적재 리포트의 미매칭으로 보고됩니다.
--
-- 이 표가 있어야 Product -> Ingredient -> Recipe -> 부족재료 -> Product 루프가 이어집니다.

INSERT INTO product_ingredient (product_id, ingredient_id, quantity_g, role, ratio)
SELECT p.product_id,
       m.ingredient_id,
       spi.quantity_g,
       LEFT(spi.role, 30),
       spi.ratio
FROM staging_product_ingredient spi
JOIN staging_ingredient_match m ON m.normalized_name = spi.normalized_name
JOIN product p ON p.source_type = spi.source_type
              AND p.source_product_id = spi.source_product_id
ON CONFLICT (product_id, ingredient_id) DO UPDATE
SET quantity_g = COALESCE(EXCLUDED.quantity_g, product_ingredient.quantity_g),
    role       = EXCLUDED.role,
    ratio      = COALESCE(EXCLUDED.ratio, product_ingredient.ratio);
