-- staging_storage_guideline -> storage_guideline 대표 행 projection/upsert.
--
-- staging 에서는 source_item_id 를 유지하지만 최종 테이블에는 저장하지 않습니다.
-- 최종 조회 키는 (ingredient_id, storage_location, storage_context) 입니다.
-- 같은 조회 키에 여러 원천 variant가 있으면 storage_tips가 있는 행을 우선하고,
-- 그래도 여러 개면 source_item_id 정렬 순서로 대표 한 건만 적재합니다.
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
    WHERE BTRIM(s.duration_text) <> ''
),
representatives AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY r.ingredient_id, r.storage_location, r.storage_context
               ORDER BY
                   CASE WHEN NULLIF(BTRIM(r.storage_tips), '') IS NOT NULL THEN 0 ELSE 1 END,
                   r.source_item_id,
                   r.source_slot
           ) AS representative_rank
    FROM ready r
),
numbered AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               ORDER BY r.ingredient_id, r.storage_location, r.storage_context
           ) AS seq
    FROM representatives r
    WHERE r.representative_rank = 1
),
id_base AS (
    SELECT COALESCE(MAX(storage_id), 0) AS base FROM storage_guideline
)
INSERT INTO storage_guideline (
    storage_id, source_food_name, source_food_subtitle,
    source_slot, storage_location, storage_context,
    duration_min, duration_max, duration_unit, duration_text, storage_tips, ingredient_id
)
SELECT b.base + n.seq,
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
ON CONFLICT (ingredient_id, storage_location, storage_context) DO UPDATE
SET source_food_name     = EXCLUDED.source_food_name,
    source_food_subtitle = COALESCE(EXCLUDED.source_food_subtitle, storage_guideline.source_food_subtitle),
    duration_min         = COALESCE(EXCLUDED.duration_min, storage_guideline.duration_min),
    duration_max         = COALESCE(EXCLUDED.duration_max, storage_guideline.duration_max),
    duration_unit        = COALESCE(EXCLUDED.duration_unit, storage_guideline.duration_unit),
    duration_text        = EXCLUDED.duration_text,
    storage_tips         = COALESCE(EXCLUDED.storage_tips, storage_guideline.storage_tips),
    updated_at           = NOW();
