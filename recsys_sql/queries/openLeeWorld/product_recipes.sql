-- name: product_recipes
-- owner: openLeeWorld
-- description: 상품으로 만들 수 있는 레시피. 재료 경유로 찾고 커버리지 요약을 붙입니다
-- params: product_id:int, max_results:int
--
-- `GET /products/{productId}/recipes` 용입니다.
--
-- ERD 17절은 `recipe_product` 를 먼저 보라고 하지만 **그 표가 지금 0행입니다.**
-- 어떤 레시피의 어떤 재료 자리에 어떤 상품을 밀지는 사람이 정하는 큐레이션 값이라
-- 자동으로 채우면 의미가 없어집니다. 그래서 ERD 18절의 재료 경유를 본선으로 씁니다.
--
--   product -> product_ingredient -> ingredient -> recipe_ingredient -> recipe
--
-- `recipe_product` 가 채워지면 그 우선순위가 먼저 오도록 LEFT JOIN 해 두었습니다.
-- 지금은 전부 NULL 이라 정렬에 영향이 없습니다.
--
-- `ingredient_summary` 는 api_spec 16절이 요구하는 값입니다. 이 상품 하나로 레시피의
-- 필수 재료 중 몇 개가 채워지는지를 셉니다. 상비재료는 채워진 것으로 칩니다.

WITH base AS (
    SELECT pi.ingredient_id
    FROM product_ingredient pi
    WHERE pi.product_id = :product_id
      AND pi.role = 'PRIMARY'
),
candidate AS (
    SELECT DISTINCT ri.recipe_id
    FROM base b
    JOIN recipe_ingredient ri ON ri.ingredient_id = b.ingredient_id
),
summary AS (
    SELECT ri.recipe_id,
           COUNT(*) FILTER (WHERE ri.is_required) AS total_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND (b.ingredient_id IS NOT NULL OR i.is_pantry)
           ) AS matched_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND b.ingredient_id IS NULL AND NOT i.is_pantry
           ) AS missing_count
    FROM candidate c
    JOIN recipe_ingredient ri ON ri.recipe_id = c.recipe_id
    JOIN ingredient i         ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN base b          ON b.ingredient_id = ri.ingredient_id
    GROUP BY ri.recipe_id
)
SELECT r.recipe_id,
       r.name,
       r.image_url,
       r.difficulty,
       r.prep_time_min,
       r.cook_time_min,
       r.servings,
       r.cooking_method,
       s.total_count,
       s.matched_count,
       s.missing_count
FROM summary s
JOIN recipe r              ON r.recipe_id = s.recipe_id
LEFT JOIN recipe_product rp ON rp.recipe_id = r.recipe_id
                           AND rp.product_id = :product_id
WHERE s.total_count > 0
ORDER BY COALESCE(rp.recommendation_priority, 0) DESC,
         s.missing_count ASC,
         r.cook_time_min ASC NULLS LAST,
         r.recipe_id ASC
LIMIT :max_results;
