-- name: bubble_candidate_counts
-- owner: openLeeWorld
-- description: 활성 버블별 후보 수. min_candidates 미달이면 화면에 내면 안 됩니다
-- params:
--
-- 버블은 눌렀을 때 결과가 비면 안 됩니다. 데이터가 바뀌면 후보가 줄 수 있어
-- (`전자레인지·에어프라이어` 가 28건이었습니다) 이 쿼리를 pytest 로 고정합니다.

WITH ingredient_stats AS (
    SELECT ri.recipe_id,
           COUNT(*) AS total,
           i.ingredient_category_id,
           cat.name AS category_name
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN category cat ON cat.category_id = i.ingredient_category_id
    WHERE NOT i.is_pantry
    GROUP BY ri.recipe_id, i.ingredient_category_id, cat.name
),
per_recipe AS (
    SELECT recipe_id, SUM(total) AS total
    FROM ingredient_stats
    GROUP BY recipe_id
),
step_stats AS (
    SELECT recipe_id, COUNT(*) AS steps FROM recipe_step GROUP BY recipe_id
)
SELECT b.keyword_id,
       b.label,
       b.min_candidates,
       COUNT(r.recipe_id) AS candidates,
       COUNT(r.recipe_id) >= b.min_candidates AS is_servable
FROM bubble_keyword b
LEFT JOIN recipe r ON CASE b.rule_type
        WHEN 'ingredient_count' THEN
            (SELECT total FROM per_recipe p WHERE p.recipe_id = r.recipe_id) <= (b.rule_spec ->> 'value')::int
        WHEN 'step_count' THEN
            (SELECT steps FROM step_stats s WHERE s.recipe_id = r.recipe_id) <= (b.rule_spec ->> 'value')::int
        WHEN 'ingredient_category' THEN
            CASE b.rule_spec ->> 'op'
                WHEN 'ratio_gte' THEN (
                    SELECT SUM(g.total) FILTER (
                        WHERE g.category_name = ANY (
                            ARRAY(SELECT jsonb_array_elements_text(b.rule_spec -> 'category_names'))
                        )
                    )::float / NULLIF(SUM(g.total), 0)
                    FROM ingredient_stats g WHERE g.recipe_id = r.recipe_id
                ) >= (b.rule_spec ->> 'value')::float
                ELSE EXISTS (
                    SELECT 1 FROM ingredient_stats g
                    WHERE g.recipe_id = r.recipe_id
                      AND g.category_name = ANY (
                          ARRAY(SELECT jsonb_array_elements_text(b.rule_spec -> 'category_names'))
                      )
                )
            END
        WHEN 'cook_time' THEN
            r.cook_time_min IS NOT NULL AND r.cook_time_min <= (b.rule_spec ->> 'value')::int
        WHEN 'servings' THEN
            r.servings IS NOT NULL AND r.servings <= (b.rule_spec ->> 'value')::numeric
        ELSE FALSE
      END
WHERE b.is_active
GROUP BY b.keyword_id, b.label, b.min_candidates, b.display_order
ORDER BY b.display_order;
