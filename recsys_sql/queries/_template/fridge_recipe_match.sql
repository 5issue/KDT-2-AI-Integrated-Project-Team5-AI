-- name: fridge_recipe_match
-- owner: _template
-- description: 사용자 냉장고 재료로 만들 수 있는 레시피를 필수 재료 커버리지 순으로 추천
-- params: user_id:int, min_coverage:float, max_results:int
--
-- 상비재료(is_pantry)는 집에 늘 있다고 보고 냉장고에 없어도 보유한 것으로 칩니다.
-- 유통기한이 지난 재료는 제외합니다.

WITH fridge AS (
    SELECT DISTINCT uf.ingredient_id
    FROM user_fridge uf
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
),
coverage AS (
    SELECT ri.recipe_id,
           COUNT(*) FILTER (WHERE ri.is_required) AS required_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND (f.ingredient_id IS NOT NULL OR i.is_pantry)
           ) AS covered_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND f.ingredient_id IS NULL AND NOT i.is_pantry
           ) AS missing_count
    FROM recipe_ingredient ri
    JOIN ingredient i     ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN fridge f    ON f.ingredient_id = ri.ingredient_id
    GROUP BY ri.recipe_id
)
SELECT r.recipe_id,
       r.name,
       r.difficulty,
       r.cook_time_min,
       c.required_count,
       c.covered_count,
       c.missing_count,
       ROUND(c.covered_count::numeric / c.required_count, 3) AS coverage
FROM coverage c
JOIN recipe r ON r.recipe_id = c.recipe_id
WHERE c.required_count > 0
  AND c.covered_count::numeric / c.required_count >= :min_coverage
ORDER BY coverage DESC,
         c.missing_count ASC,
         r.cook_time_min ASC NULLS LAST,
         r.recipe_id ASC
LIMIT :max_results;
