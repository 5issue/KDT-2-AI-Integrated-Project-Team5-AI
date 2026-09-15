-- name: my_recipe_candidates
-- owner: openLeeWorld
-- description: 마이냉장고 기반 레시피 추천. 부족한 재료 목록까지 함께 냅니다
-- params: user_id:int, min_match_rate:float, max_results:int
--
-- `GET /recommendations/my-recipes` 용입니다.
--
-- `_template/fridge_recipe_match` 와 판정 규칙은 같습니다. 다른 점은 응답 모양입니다.
-- api_spec 21절이 부족 재료 **목록**까지 요구해서, 개수만 내는 쪽으로는 부족합니다.
-- 화면에서 개수를 보고 다시 재료를 물으면 왕복이 레시피 수만큼 늘어납니다.
--
-- 보유 판정: 냉장고 상품의 PRIMARY 재료 + 상비재료.
-- 냉장고 재료를 하나도 쓰지 않는 레시피는 후보에서 먼저 잘라냅니다.

WITH fridge AS (
    SELECT DISTINCT pi.ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
),
candidate AS (
    SELECT DISTINCT ri.recipe_id
    FROM fridge f
    JOIN recipe_ingredient ri ON ri.ingredient_id = f.ingredient_id
    WHERE ri.is_required
),
match AS (
    SELECT ri.recipe_id,
           COUNT(*) FILTER (WHERE ri.is_required) AS required_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND (f.ingredient_id IS NOT NULL OR i.is_pantry)
           ) AS available_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND f.ingredient_id IS NULL AND NOT i.is_pantry
           ) AS missing_count,
           COALESCE(
               JSONB_AGG(
                   JSONB_BUILD_OBJECT('ingredient_id', i.ingredient_id, 'name', i.name)
                   ORDER BY i.name
               ) FILTER (
                   WHERE ri.is_required AND f.ingredient_id IS NULL AND NOT i.is_pantry
               ),
               '[]'::jsonb
           ) AS missing_ingredients
    FROM candidate c
    JOIN recipe_ingredient ri ON ri.recipe_id = c.recipe_id
    JOIN ingredient i         ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN fridge f        ON f.ingredient_id = ri.ingredient_id
    GROUP BY ri.recipe_id
)
SELECT r.recipe_id,
       r.name,
       r.image_url,
       r.difficulty,
       r.cook_time_min,
       r.servings,
       m.required_count,
       m.available_count,
       m.missing_count,
       ROUND(m.available_count::numeric / m.required_count, 3) AS match_rate,
       m.missing_ingredients
FROM match m
JOIN recipe r ON r.recipe_id = m.recipe_id
WHERE m.required_count > 0
  AND m.available_count::numeric / m.required_count >= :min_match_rate
ORDER BY match_rate DESC,
         m.missing_count ASC,
         r.cook_time_min ASC NULLS LAST,
         r.recipe_id ASC
LIMIT :max_results;
