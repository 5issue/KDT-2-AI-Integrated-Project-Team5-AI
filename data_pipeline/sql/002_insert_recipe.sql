-- staging_recipe -> recipe upsert.
-- 자연키는 (source_type, source_recipe_id). recipe_source_unique_idx 가 이미 있습니다.
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
