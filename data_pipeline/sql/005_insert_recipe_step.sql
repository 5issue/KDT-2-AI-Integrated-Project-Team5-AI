-- staging_recipe_step -> recipe_step upsert.
-- recipe_id 는 002 가 넣은 recipe 를 자연키로 되짚어 가져옵니다.
--
-- 단계가 줄어든 레시피를 재적재하면 예전 뒤쪽 단계가 남습니다(PK 가 (recipe_id, step_no) 라
-- upsert 만으로는 안 지워짐). 그래서 이번에 온 레시피에 한해 초과 단계를 먼저 지웁니다.
-- 단계가 아예 없는 레시피는 staging 에 행이 없으므로 건드리지 않습니다.

WITH incoming AS (
    SELECT r.recipe_id, MAX(srs.step_no) AS max_step
    FROM staging_recipe_step srs
    JOIN recipe r ON r.source_type = srs.source_type
                 AND r.source_recipe_id = srs.source_id
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
