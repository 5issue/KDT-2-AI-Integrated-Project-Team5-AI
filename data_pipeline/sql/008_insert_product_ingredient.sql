-- staging_product_ingredient -> product_ingredient.
--
-- ingredient_id 는 두 곳에서 옵니다.
-- 1. staging 에 직접 실려 온 값 (상품명/카테고리로 유추한 경우)
-- 2. 3단계가 채운 staging_ingredient_match (raw 의 ingredients 로 들어온 경우)
-- 둘 다 없으면 빠지고 적재 리포트의 미매칭으로 보고됩니다.
--
-- 이 표가 있어야 Product -> Ingredient -> Recipe -> 부족재료 -> Product 루프가 이어집니다.

INSERT INTO product_ingredient (product_id, ingredient_id, quantity_g, role, ratio)
SELECT DISTINCT ON (p.product_id, COALESCE(spi.ingredient_id, m.ingredient_id))
       p.product_id,
       COALESCE(spi.ingredient_id, m.ingredient_id),
       spi.quantity_g,
       LEFT(spi.role, 30),
       spi.ratio
FROM staging_product_ingredient spi
LEFT JOIN staging_ingredient_match m ON m.normalized_name = spi.normalized_name
JOIN product p ON p.source_type = spi.source_type
              AND p.source_product_id = spi.source_product_id
WHERE COALESCE(spi.ingredient_id, m.ingredient_id) IS NOT NULL
ON CONFLICT (product_id, ingredient_id) DO UPDATE
SET quantity_g = COALESCE(EXCLUDED.quantity_g, product_ingredient.quantity_g),
    role       = EXCLUDED.role,
    ratio      = COALESCE(EXCLUDED.ratio, product_ingredient.ratio);
