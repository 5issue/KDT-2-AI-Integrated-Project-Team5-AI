-- name: bubble_recipe_candidates
-- owner: openLeeWorld
-- description: 버블 하나를 눌렀을 때 나올 레시피 후보. bubble_keyword.rule_spec 을 그대로 해석
-- params: keyword_id:str, max_results:int
--
-- 버블마다 조건이 다르지만 화면은 같은 목록을 받습니다. 그래서 rule_type 별 분기를
-- SQL 안에 두고 keyword_id 하나만 받습니다. 규칙을 바꿀 때 코드를 고치지 않아도 됩니다.
--
-- 상비재료(is_pantry)는 재료 수와 비율 계산에서 뺍니다. 소금·설탕까지 세면
-- "재료 적게 드는 요리" 에 걸릴 레시피가 거의 없습니다(재료줄의 34%가 상비재료).

WITH bubble AS (
    SELECT keyword_id, rule_type, rule_spec, min_candidates
    FROM bubble_keyword
    WHERE keyword_id = :keyword_id
      AND is_active
),
-- 상비재료를 뺀 레시피별 재료 집계. 분류 이름까지 같이 들고 옵니다.
ingredient_stats AS (
    SELECT ri.recipe_id,
           COUNT(*) AS total,
           COUNT(*) FILTER (
               WHERE cat.name = ANY (
                   SELECT jsonb_array_elements_text(b.rule_spec -> 'category_names')
                   FROM bubble b
                   WHERE b.rule_spec ? 'category_names'
               )
           ) AS in_category
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN category cat ON cat.category_id = i.ingredient_category_id
    WHERE NOT i.is_pantry
    GROUP BY ri.recipe_id
),
step_stats AS (
    SELECT recipe_id, COUNT(*) AS steps
    FROM recipe_step
    GROUP BY recipe_id
)
SELECT r.recipe_id,
       r.name,
       r.image_url,
       r.cook_time_min,
       r.servings,
       COALESCE(s.steps, 0) AS step_count,
       COALESCE(g.total, 0) AS ingredient_count
FROM recipe r
CROSS JOIN bubble b
LEFT JOIN ingredient_stats g ON g.recipe_id = r.recipe_id
LEFT JOIN step_stats s ON s.recipe_id = r.recipe_id
WHERE CASE b.rule_type
        WHEN 'ingredient_count' THEN
            COALESCE(g.total, 0) > 0
            AND g.total <= (b.rule_spec ->> 'value')::int
        WHEN 'step_count' THEN
            COALESCE(s.steps, 0) > 0
            AND s.steps <= (b.rule_spec ->> 'value')::int
        WHEN 'ingredient_category' THEN
            CASE b.rule_spec ->> 'op'
                WHEN 'ratio_gte' THEN
                    COALESCE(g.total, 0) > 0
                    AND g.in_category::float / g.total >= (b.rule_spec ->> 'value')::float
                ELSE COALESCE(g.in_category, 0) > 0
            END
        WHEN 'cook_time' THEN
            r.cook_time_min IS NOT NULL
            AND r.cook_time_min <= (b.rule_spec ->> 'value')::int
        WHEN 'servings' THEN
            r.servings IS NOT NULL
            AND r.servings <= (b.rule_spec ->> 'value')::numeric
        ELSE FALSE
      END
ORDER BY COALESCE(s.steps, 999), r.recipe_id
LIMIT :max_results;
