-- name: product_recipes
-- owner: openLeeWorld
-- description: 상품으로 만들 수 있는 레시피. 재료 경유로 찾고 커버리지 요약을 붙입니다
-- params: product_id:int, max_results:int, skip:int
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

WITH RECURSIVE base_direct AS (
    SELECT pi.ingredient_id
    FROM product_ingredient pi
    WHERE pi.product_id = :product_id
      AND pi.role = 'PRIMARY'
),
base(ingredient_id) AS (
    SELECT ingredient_id
    FROM base_direct
    UNION
    SELECT i.parent_ingredient_id
    FROM base b
    JOIN ingredient i ON i.ingredient_id = b.ingredient_id
    WHERE i.parent_ingredient_id IS NOT NULL
),
candidate AS (
    -- 필수 재료로 걸린 것만 후보입니다. 선택 재료만 겹치는 레시피를 넣으면
    -- "이 상품으로 만들 수 있는 요리" 에 필수 재료를 하나도 못 채우는 레시피가 섞입니다.
    SELECT DISTINCT ri.recipe_id
    FROM base b
    JOIN recipe_ingredient ri ON ri.ingredient_id = b.ingredient_id
                             AND ri.is_required
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
       s.missing_count,
       -- api_spec 16절의 has_next 용. 자르기 전 전체 개수입니다.
       COUNT(*) OVER () AS total_recipes
FROM summary s
JOIN recipe r              ON r.recipe_id = s.recipe_id
-- recipe_product 의 키는 (recipe_id, ingredient_id, product_id) 입니다. 재료 자리까지
-- 포함하므로, 한 상품이 같은 레시피의 두 자리에 지정되면 행이 둘입니다. 그대로 조인하면
-- LEFT JOIN 이 summary 행을 그 수만큼 복제해 같은 레시피가 목록에 두 번 나오고,
-- 그 중복이 LIMIT 앞에서 생겨 요청보다 적은 레시피가 나갑니다.
-- 여기서는 "이 상품이 이 레시피에서 갖는 우선순위" 하나만 있으면 되므로 먼저 접습니다.
LEFT JOIN LATERAL (
    SELECT MAX(designated.recommendation_priority) AS recommendation_priority
    FROM recipe_product designated
    WHERE designated.recipe_id = r.recipe_id
      AND designated.product_id = :product_id
) rp ON TRUE
WHERE s.total_count > 0
ORDER BY COALESCE(rp.recommendation_priority, 0) DESC,
         s.missing_count ASC,
         r.cook_time_min ASC NULLS LAST,
         r.recipe_id ASC
LIMIT :max_results
OFFSET :skip;
