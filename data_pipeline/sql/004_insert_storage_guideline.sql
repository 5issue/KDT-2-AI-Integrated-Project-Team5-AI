-- staging_storage_guideline -> storage_guideline upsert.
--
-- 자연키는 (ingredient_id, source_item_id, source_slot, storage_location, storage_context) 이고
-- uq_storage_guideline_source_rule 유니크 제약이 이미 있습니다.
--
-- storage_id 는 BIGINT 인데 시퀀스가 없습니다(스키마 후속 과제).
-- 지금은 MAX + ROW_NUMBER 로 채웁니다. 동시에 두 명이 적재하면 충돌할 수 있으므로
-- 적재는 한 번에 한 명만 수행하거나 storage_id 를 IDENTITY 로 바꾸는 편이 안전합니다.
--
-- storage_location / storage_context 는 source_slot 에서 결정적으로 파생된 값입니다
-- (domain.SLOT_DERIVATION 이 DB 의 CHECK 제약과 1:1 로 대응합니다).

WITH ready AS (
    SELECT s.*, m.ingredient_id
    FROM staging_storage_guideline s
    JOIN staging_ingredient_match m ON m.normalized_name = s.normalized_name
),
numbered AS (
    SELECT r.*,
           ROW_NUMBER() OVER (ORDER BY r.source_item_id, r.source_slot) AS seq
    FROM ready r
),
id_base AS (
    SELECT COALESCE(MAX(storage_id), 0) AS base FROM storage_guideline
)
INSERT INTO storage_guideline (
    storage_id, source_item_id, source_food_name, source_food_subtitle,
    source_slot, storage_location, storage_context,
    duration_min, duration_max, duration_unit, duration_text, storage_tips, ingredient_id
)
SELECT b.base + n.seq,
       n.source_item_id,
       n.source_food_name,
       n.source_food_subtitle,
       n.source_slot,
       n.storage_location,
       n.storage_context,
       n.duration_min,
       n.duration_max,
       n.duration_unit,
       n.duration_text,
       n.storage_tips,
       n.ingredient_id
FROM numbered n
CROSS JOIN id_base b
ON CONFLICT (ingredient_id, source_item_id, source_slot, storage_location, storage_context) DO UPDATE
SET source_food_name     = EXCLUDED.source_food_name,
    source_food_subtitle = COALESCE(EXCLUDED.source_food_subtitle, storage_guideline.source_food_subtitle),
    duration_min         = COALESCE(EXCLUDED.duration_min, storage_guideline.duration_min),
    duration_max         = COALESCE(EXCLUDED.duration_max, storage_guideline.duration_max),
    duration_unit        = COALESCE(EXCLUDED.duration_unit, storage_guideline.duration_unit),
    duration_text        = EXCLUDED.duration_text,
    storage_tips         = COALESCE(EXCLUDED.storage_tips, storage_guideline.storage_tips),
    updated_at           = NOW();
