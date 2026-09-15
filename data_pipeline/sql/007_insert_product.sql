-- staging_product -> product.
--
-- 자연키는 (source_type, source_product_id) 인데 유니크 제약이 없어 ON CONFLICT 를
-- 쓸 수 없습니다. NOT EXISTS 로 넣고, 이미 있는 것은 따로 갱신합니다.
-- 유니크 인덱스를 만들면 이 파일을 upsert 로 단순화할 수 있습니다(후속 과제).
--
-- category_id 는 006 이 넣은 category 의 metadata.path 로 되짚습니다.
-- sku 는 UNIQUE 라 빈 문자열을 넣으면 두 번째 행에서 충돌합니다. NULL 로 둡니다.
--
-- **자연키는 자를 값과 비교할 값이 같아야 합니다.** 예전에는 INSERT 가 LEFT(..., 30) 으로
-- 잘라 넣으면서 NOT EXISTS 는 자르지 않은 staging 값과 비교했습니다. 길이를 넘는 키가
-- 하나라도 들어오면 다음 적재에서 기존 행을 못 찾아 같은 상품이 또 들어갑니다.
-- 아래 CTE 에서 한 번만 자르고, 조회·삽입·갱신이 모두 그 값을 씁니다.

WITH normalized AS (
    SELECT sp.*,
           LEFT(sp.source_type, 30)        AS key_source_type,
           LEFT(sp.source_product_id, 100) AS key_source_product_id
    FROM staging_product sp
)
INSERT INTO product (
    sku, name, category_id, product_type, storage_type, origin_country,
    weight_g, unit_count, price, stock_quantity, metadata, source_type, source_product_id
)
SELECT NULLIF(BTRIM(COALESCE(sp.sku, '')), ''),
       LEFT(sp.name, 255),
       cat.category_id,
       LEFT(sp.product_type, 30),
       LEFT(sp.storage_type, 20),
       LEFT(sp.origin_country, 100),
       sp.weight_g,
       sp.unit_count,
       sp.price,
       sp.stock_quantity,
       sp.metadata,
       sp.key_source_type,
       sp.key_source_product_id
FROM normalized sp
LEFT JOIN category cat ON cat.metadata ->> 'path' = sp.category_path
WHERE NOT EXISTS (
    SELECT 1 FROM product p
    WHERE p.source_type = sp.key_source_type
      AND p.source_product_id = sp.key_source_product_id
);

WITH normalized AS (
    SELECT sp.*,
           LEFT(sp.source_type, 30)        AS key_source_type,
           LEFT(sp.source_product_id, 100) AS key_source_product_id
    FROM staging_product sp
)
UPDATE product p
SET name           = LEFT(sp.name, 255),
    price          = sp.price,
    category_id    = COALESCE(cat.category_id, p.category_id),
    product_type   = LEFT(sp.product_type, 30),
    storage_type   = COALESCE(LEFT(sp.storage_type, 20), p.storage_type),
    origin_country = COALESCE(LEFT(sp.origin_country, 100), p.origin_country),
    weight_g       = COALESCE(sp.weight_g, p.weight_g),
    unit_count     = COALESCE(sp.unit_count, p.unit_count),
    stock_quantity = COALESCE(sp.stock_quantity, p.stock_quantity),
    metadata       = sp.metadata,
    updated_at     = NOW()
FROM normalized sp
LEFT JOIN category cat ON cat.metadata ->> 'path' = sp.category_path
WHERE p.source_type = sp.key_source_type
  AND p.source_product_id = sp.key_source_product_id;
