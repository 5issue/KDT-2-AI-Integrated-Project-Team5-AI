-- staging_recipe_step -> recipe_step upsert.
-- recipe_id 는 002 가 넣은 recipe 를 자연키로 되짚어 가져옵니다.
--
-- 단계가 줄어든 레시피를 재적재하면 예전 뒤쪽 단계가 남습니다(PK 가 (recipe_id, step_no) 라
-- upsert 만으로는 안 지워짐). 그래서 이번에 온 레시피에 한해 초과 단계를 먼저 지웁니다.
--
-- 기준을 staging_recipe_step 이 아니라 **staging_recipe** 로 잡습니다. 단계 테이블만 보면
-- 이번에 단계가 0개로 온 레시피는 아예 행이 없어서 옛 단계가 통째로 살아남습니다.
-- "이번 적재에 포함된 레시피" 는 staging_recipe 가 알고 있으므로 거기서 출발합니다.
-- 단계가 0개면 max_step 이 NULL 이고, COALESCE 로 0 을 만들어 전부 지웁니다.

WITH incoming AS (
    SELECT r.recipe_id,
           COALESCE(MAX(srs.step_no), 0) AS max_step
    FROM staging_recipe sr
    JOIN recipe r ON r.source_type = sr.source_type
                 AND r.source_recipe_id = sr.source_id
    LEFT JOIN staging_recipe_step srs ON srs.source_type = sr.source_type
                                     AND srs.source_id = sr.source_id
    GROUP BY r.recipe_id
)
DELETE FROM recipe_step rs
USING incoming i
WHERE rs.recipe_id = i.recipe_id
  AND rs.step_no > i.max_step;

INSERT INTO recipe_step (recipe_id, step_no, instruction, image_url)
SELECT r.recipe_id,
       srs.step_no,
       srs.instruction,
       srs.image_url
FROM staging_recipe_step srs
JOIN recipe r ON r.source_type = srs.source_type
             AND r.source_recipe_id = srs.source_id
ON CONFLICT (recipe_id, step_no) DO UPDATE
SET instruction = COALESCE(EXCLUDED.instruction, recipe_step.instruction),
    image_url   = COALESCE(EXCLUDED.image_url, recipe_step.image_url),
    updated_at  = NOW();
