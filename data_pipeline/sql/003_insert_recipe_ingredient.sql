-- staging_recipe_ingredient -> recipe_ingredient upsert.
-- ingredient_id 는 3단계가 채운 staging_ingredient_match 에서 가져옵니다.
-- 매칭되지 않은 재료는 여기서 빠지고 적재 리포트의 미매칭 목록으로 보고됩니다.
--
-- 한 레시피에서 서로 다른 표기가 같은 재료로 매칭될 수 있어(예: '다진 마늘'과 '마늘')
-- 먼저 중복을 제거합니다. 하지 않으면 ON CONFLICT 가 같은 행을 두 번 건드려 실패합니다.

WITH deduped AS (
    SELECT DISTINCT ON (sri.source_type, sri.source_id, m.ingredient_id)
           sri.source_type,
           sri.source_id,
           m.ingredient_id,
           sri.quantity,
           sri.unit,
           sri.is_required,
           sri.purpose
    FROM staging_recipe_ingredient sri
    JOIN staging_ingredient_match m ON m.normalized_name = sri.normalized_name
    ORDER BY sri.source_type, sri.source_id, m.ingredient_id, sri.is_required DESC, sri.line_no
)
INSERT INTO recipe_ingredient (recipe_id, ingredient_id, quantity, unit, is_required, purpose)
SELECT r.recipe_id,
       d.ingredient_id,
       d.quantity,
       LEFT(d.unit, 30),
       d.is_required,
       LEFT(d.purpose, 50)
FROM deduped d
JOIN recipe r ON r.source_type = d.source_type
             AND r.source_recipe_id = d.source_id
ON CONFLICT (recipe_id, ingredient_id) DO UPDATE
SET quantity    = COALESCE(EXCLUDED.quantity, recipe_ingredient.quantity),
    unit        = COALESCE(EXCLUDED.unit, recipe_ingredient.unit),
    is_required = EXCLUDED.is_required,
    purpose     = COALESCE(EXCLUDED.purpose, recipe_ingredient.purpose);
