-- staging_recipe -> recipe upsert.
--
-- 자연키는 (source_type, source_recipe_id) 입니다. 실제 스키마에 이 조합의 유니크 인덱스
-- (recipe_source_unique_idx)가 이미 있어서 별도 마이그레이션이 필요 없습니다.
-- name 을 키로 쓰지 않으므로 같은 이름의 레시피가 여러 출처에서 들어와도 안전합니다.
--
-- tags 는 실제 스키마에서 text[] 입니다(문서의 TEXT 와 다름). 그대로 배열로 넣습니다.
--
-- 재적재 시 이미 채워둔 값을 null 로 덮어쓰지 않도록 COALESCE 로 보수적으로 갱신합니다.

INSERT INTO recipe (
    name, description, category_id, cuisine_type, difficulty,
    prep_time_min, cook_time_min, servings, cooking_method, nutrition, tags,
    source_type, source_recipe_id
)
SELECT sr.name,
       sr.description,
       NULL,
       sr.cuisine_type,
       sr.difficulty,
       sr.prep_time_min,
       sr.cook_time_min,
       sr.servings,
       sr.cooking_method,
       sr.nutrition,
       sr.tags,
       sr.source_type,
       sr.source_id
FROM staging_recipe sr
ON CONFLICT (source_type, source_recipe_id) DO UPDATE
SET name           = EXCLUDED.name,
    description    = COALESCE(EXCLUDED.description, recipe.description),
    cuisine_type   = COALESCE(EXCLUDED.cuisine_type, recipe.cuisine_type),
    difficulty     = COALESCE(EXCLUDED.difficulty, recipe.difficulty),
    prep_time_min  = COALESCE(EXCLUDED.prep_time_min, recipe.prep_time_min),
    cook_time_min  = COALESCE(EXCLUDED.cook_time_min, recipe.cook_time_min),
    servings       = COALESCE(EXCLUDED.servings, recipe.servings),
    cooking_method = COALESCE(EXCLUDED.cooking_method, recipe.cooking_method),
    nutrition      = CASE WHEN EXCLUDED.nutrition = '{}'::jsonb THEN recipe.nutrition ELSE EXCLUDED.nutrition END,
    tags           = CASE WHEN EXCLUDED.tags = '{}'::text[] THEN recipe.tags ELSE EXCLUDED.tags END;
