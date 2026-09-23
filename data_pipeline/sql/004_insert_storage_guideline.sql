-- staging_storage_guideline -> storage_guideline 대표 행 projection/upsert.
--
-- staging 에서는 source_item_id 를 유지하지만 최종 테이블에는 저장하지 않습니다.
-- 최종 조회 키는 (ingredient_id, storage_location, storage_context) 입니다.
-- 같은 조회 키에 여러 원천 variant가 있으면 기간이 모두 같은 경우에만
-- storage_tips가 있는 행을 우선해 대표 한 건을 적재합니다. 기간이 다르면 staging에
-- PERIOD_CONFLICT 상태와 사유를 남기고 최종 테이블에는 적재하지 않습니다.
--
-- storage_id 는 migration에서 GENERATED ALWAYS IDENTITY로 관리하므로 INSERT 대상에서
-- 제외하고 DB가 값을 만들게 합니다.
--
-- storage_location / storage_context 는 source_slot 에서 결정적으로 파생된 값입니다
-- (domain.SLOT_DERIVATION 이 DB 의 CHECK 제약과 1:1 로 대응합니다).

WITH ready AS (
    SELECT s.source_item_id,
           s.source_slot,
           m.ingredient_id,
           s.storage_location,
           s.storage_context,
           ROW(
               s.duration_min,
               s.duration_max,
               s.duration_unit,
               CASE
                   WHEN s.duration_min IS NULL
                    AND s.duration_max IS NULL
                    AND s.duration_unit IS NULL
                   THEN BTRIM(s.duration_text)
               END
           ) AS period_signature
    FROM staging_storage_guideline s
    JOIN staging_ingredient_match m ON m.normalized_name = s.normalized_name
    WHERE BTRIM(s.duration_text) <> ''
),
period_conflicts AS (
    SELECT ingredient_id,
           storage_location,
           storage_context,
           COUNT(DISTINCT period_signature) AS period_count
    FROM ready
    GROUP BY ingredient_id, storage_location, storage_context
    HAVING COUNT(DISTINCT period_signature) > 1
)
UPDATE staging_storage_guideline s
SET review_status = 'PERIOD_CONFLICT',
    review_detail = '동일 조회 키에 서로 다른 기간 ' || c.period_count || '개'
FROM staging_ingredient_match m,
     period_conflicts c
WHERE m.normalized_name = s.normalized_name
  AND c.ingredient_id = m.ingredient_id
  AND c.storage_location = s.storage_location
  AND c.storage_context = s.storage_context;

WITH ready AS (
    SELECT s.*, m.ingredient_id
    FROM staging_storage_guideline s
    JOIN staging_ingredient_match m ON m.normalized_name = s.normalized_name
    WHERE BTRIM(s.duration_text) <> ''
      AND s.review_status IS NULL
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
selected AS (
    SELECT r.*
    FROM representatives r
    WHERE r.representative_rank = 1
)
INSERT INTO storage_guideline (
    source_food_name, source_food_subtitle,
    source_slot, storage_location, storage_context,
    duration_min, duration_max, duration_unit, duration_text, storage_tips, ingredient_id
)
SELECT n.source_food_name,
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
FROM selected n
ON CONFLICT (ingredient_id, storage_location, storage_context) DO UPDATE
SET source_food_name     = EXCLUDED.source_food_name,
    source_food_subtitle = COALESCE(EXCLUDED.source_food_subtitle, storage_guideline.source_food_subtitle),
    duration_min         = COALESCE(EXCLUDED.duration_min, storage_guideline.duration_min),
    duration_max         = COALESCE(EXCLUDED.duration_max, storage_guideline.duration_max),
    duration_unit        = COALESCE(EXCLUDED.duration_unit, storage_guideline.duration_unit),
    duration_text        = EXCLUDED.duration_text,
    storage_tips         = COALESCE(EXCLUDED.storage_tips, storage_guideline.storage_tips),
    updated_at           = NOW();
