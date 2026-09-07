-- staging_embedding -> 본 테이블 embedding 컬럼 반영.
-- Batch API 의 /v1/embeddings 결과를 적재한 뒤 실행합니다.
--
-- target_key 는 각 테이블의 자연키를 씁니다.
--   recipe     : '<source_type>:<source_recipe_id>'
--   ingredient : source_identity_key  (예: 'K-FIND:01:01018:국수')
--   product    : '<source_type>:<source_product_id>'

UPDATE recipe r
SET embedding = se.embedding
FROM staging_embedding se
WHERE se.target_table = 'recipe'
  AND se.target_key = r.source_type || ':' || r.source_recipe_id;

UPDATE ingredient i
SET embedding = se.embedding
FROM staging_embedding se
WHERE se.target_table = 'ingredient'
  AND se.target_key = i.source_identity_key;

UPDATE product p
SET embedding  = se.embedding,
    updated_at = NOW()
FROM staging_embedding se
WHERE se.target_table = 'product'
  AND se.target_key = p.source_type || ':' || p.source_product_id;
