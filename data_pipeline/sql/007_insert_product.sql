-- staging_product -> product.
--
-- 자연키는 (source_type, source_product_id) 인데 유니크 제약이 없어 ON CONFLICT 를
-- 쓸 수 없습니다. NOT EXISTS 로 넣고, 이미 있는 것은 따로 갱신합니다.
-- 유니크 인덱스를 만들면 이 파일을 upsert 로 단순화할 수 있습니다(후속 과제).
--
-- category_id 는 006 이 넣은 category 의 metadata.path 로 되짚습니다.
-- sku 는 UNIQUE 라 빈 문자열을 넣으면 두 번째 행에서 충돌합니다. NULL 로 둡니다.

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
       LEFT(sp.source_type, 30),
       LEFT(sp.source_product_id, 100)
FROM staging_product sp
LEFT JOIN category cat ON cat.metadata ->> 'path' = sp.category_path
WHERE NOT EXISTS (
    SELECT 1 FROM product p
    WHERE p.source_type = sp.source_type AND p.source_product_id = sp.source_product_id
);

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
FROM staging_product sp
LEFT JOIN category cat ON cat.metadata ->> 'path' = sp.category_path
WHERE p.source_type = sp.source_type
  AND p.source_product_id = sp.source_product_id;
