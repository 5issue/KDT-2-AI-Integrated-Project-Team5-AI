-- name: fridge_recipe_match
-- promoted-from: recsys_sql/queries/_template/fridge_recipe_match.sql
-- description: 사용자 냉장고 재료로 만들 수 있는 레시피를 필수 재료 커버리지 순으로 추천
-- params: $1 = user_id (bigint), $2 = min_coverage (double precision), $3 = max_results (int)
--
-- 상비재료(is_pantry)는 집에 늘 있다고 보고 냉장고에 없어도 보유한 것으로 칩니다.
-- 유통기한이 지난 재료는 제외합니다.

WITH fridge AS (
    SELECT DISTINCT uf.ingredient_id
    FROM user_fridge uf
    WHERE uf.user_id = $1
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
  -- $2 를 double precision 으로 못박습니다. numeric 으로 추론되면
  -- asyncpg 가 Decimal 을 요구해서 파이썬 float 을 그대로 넘길 수 없습니다.
  AND c.covered_count::numeric / c.required_count >= CAST($2 AS double precision)
ORDER BY coverage DESC,
         c.missing_count ASC,
         r.cook_time_min ASC NULLS LAST,
         r.recipe_id ASC
LIMIT $3;
