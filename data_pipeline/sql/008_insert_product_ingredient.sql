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
-- DISTINCT ON 은 ORDER BY 가 없으면 어느 행이 남을지 보장하지 않습니다.
-- 서로 다른 normalized_name 이 같은 ingredient_id 로 매칭되는 일이 있어서
-- (예: `계란`/`달걀`), 정렬을 안 두면 재적재마다 role 이나 quantity_g 가 달라집니다.
-- PRIMARY 를 먼저, 그다음 수치가 채워진 행, 마지막으로 이름 순으로 고정합니다.
ORDER BY p.product_id,
         COALESCE(spi.ingredient_id, m.ingredient_id),
         (spi.role = 'PRIMARY') DESC,
         spi.quantity_g DESC NULLS LAST,
         spi.ratio DESC NULLS LAST,
         spi.normalized_name
ON CONFLICT (product_id, ingredient_id) DO UPDATE
SET quantity_g = COALESCE(EXCLUDED.quantity_g, product_ingredient.quantity_g),
    role       = EXCLUDED.role,
    ratio      = COALESCE(EXCLUDED.ratio, product_ingredient.ratio);
