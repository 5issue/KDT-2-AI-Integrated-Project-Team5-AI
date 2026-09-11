-- name: product_detail
-- owner: openLeeWorld
-- description: 상품 상세. 구성 재료를 한 줄에 묶어 함께 냅니다
-- params: product_id:int
--
-- `GET /products/{productId}` 용입니다.
--
-- 구성 재료를 별도 쿼리로 나누지 않고 jsonb 배열로 함께 냅니다. 상세 화면은 둘을 항상
-- 같이 쓰고, 나누면 왕복이 두 번이 됩니다. 재료가 없는 상품(295건)도 상품 정보는
-- 나와야 하므로 LEFT JOIN 입니다.
--
-- 응답 규격(api_spec 15절)에 `image_url` 이 있지만 `product` 에 그 컬럼이 없습니다.
-- 크롤 metadata 에도 `source_url` 만 있고 이미지 주소는 없습니다. 여기서 지어낼 수
-- 없으므로 뺐습니다. 원본에 이미지가 추가되면 컬럼과 함께 올립니다.

SELECT p.product_id,
       p.name,
       p.price,
       p.weight_g,
       p.unit_count,
       p.product_type,
       p.storage_type,
       p.origin_country,
       p.stock_quantity,
       p.is_active,
       c.name AS category_name,
       COALESCE(
           JSONB_AGG(
               JSONB_BUILD_OBJECT(
                   'ingredient_id', i.ingredient_id,
                   'name', i.name,
                   'role', pi.role
               )
               ORDER BY pi.role, i.name
           ) FILTER (WHERE i.ingredient_id IS NOT NULL),
           '[]'::jsonb
       ) AS ingredients
FROM product p
LEFT JOIN category c           ON c.category_id = p.category_id
LEFT JOIN product_ingredient pi ON pi.product_id = p.product_id
LEFT JOIN ingredient i          ON i.ingredient_id = pi.ingredient_id
WHERE p.product_id = :product_id
GROUP BY p.product_id, c.name;
