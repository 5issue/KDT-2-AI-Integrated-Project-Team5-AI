-- name: recipe_detail
-- owner: openLeeWorld
-- description: 레시피 상세. 재료줄과 조리 단계를 함께 냅니다
-- params: recipe_id:int
--
-- `GET /recipes/{recipeId}` 용입니다.
--
-- 재료와 단계를 별도 왕복으로 나누지 않습니다. 상세 화면은 셋을 항상 같이 씁니다.
-- 단계가 없는 레시피(1,086건 중 일부)도 상세는 나와야 하므로 빈 배열을 냅니다.
--
-- `nutrition` 은 jsonb 그대로 냅니다. 원본마다 채워진 항목이 달라
-- (`fat_g`, `protein_g`, `sodium_mg`, `carbohydrate_g`) 컬럼으로 펴면 대부분 null 이 됩니다.

SELECT r.recipe_id,
       r.name,
       r.description,
       r.image_url,
       r.difficulty,
       r.prep_time_min,
       r.cook_time_min,
       r.servings,
       r.cooking_method,
       r.cuisine_type,
       r.nutrition,
       r.tags,
       c.name AS category_name,
       COALESCE(lines.items, '[]'::jsonb) AS ingredients,
       COALESCE(steps.items, '[]'::jsonb) AS steps
FROM recipe r
LEFT JOIN category c ON c.category_id = r.category_id
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
               JSONB_BUILD_OBJECT(
                   'ingredient_id', i.ingredient_id,
                   'name', i.name,
                   'quantity', ri.quantity,
                   'unit', ri.unit,
                   'is_required', ri.is_required,
                   'is_pantry', i.is_pantry,
                   'purpose', ri.purpose
               )
               ORDER BY ri.is_required DESC, i.name
           ) AS items
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.ingredient_id = ri.ingredient_id
    WHERE ri.recipe_id = r.recipe_id
) lines ON TRUE
LEFT JOIN LATERAL (
    SELECT JSONB_AGG(
               JSONB_BUILD_OBJECT(
                   'step_no', rs.step_no,
                   'instruction', rs.instruction,
                   'image_url', rs.image_url
               )
               ORDER BY rs.step_no
           ) AS items
    FROM recipe_step rs
    WHERE rs.recipe_id = r.recipe_id
) steps ON TRUE
WHERE r.recipe_id = :recipe_id;
