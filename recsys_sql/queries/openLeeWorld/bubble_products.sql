-- name: bubble_products
-- owner: openLeeWorld
-- description: 버블을 눌렀을 때 나올 상품. 그 버블의 레시피들이 실제로 쓰는 재료의 상품
-- params: keyword_id:str, max_results:int, skip:int
--
-- `GET /recommendations/products` 용입니다.
--
-- 버블 규칙은 전부 레시피 조건입니다(재료 수, 단계 수, 재료 분류, 조리시간).
-- 그래서 상품을 바로 고를 수 없고 레시피를 한 번 거칩니다.
--
--   bubble_recipe_candidate -> 그 레시피들의 필수 재료 -> 그 재료를 파는 상품
--
-- 후보 레시피는 `bubble_recipe_candidates` 와 같은 뷰를 봅니다. 규칙 해석이 두 벌이면
-- "버블에서 본 상품으로 만들 수 있는 레시피가 그 버블에 없는" 일이 생깁니다.
--
-- ## 정렬을 인기순으로 하지 않은 이유
--
-- `product_popularity` 와 `user_product_affinity` 가 지금 0행입니다. 주문·조회 로그가
-- 아직 없어서 점수를 만들 원천이 없습니다. 없는 값으로 정렬하면 사실상 product_id 순이
-- 되므로, 있는 신호로 정합니다: **이 버블의 레시피 중 몇 개가 이 재료를 쓰는가.**
-- 버블 안에서 쓰임새가 많은 재료의 상품을 먼저 보여 줍니다. 로그가 쌓이면
-- `popularity_score` 와 `recipe_count` 에 가중치를 둔 합으로 바꾸면 됩니다(ERD 16절).
--
-- 상비재료는 뺍니다. 어느 버블에서나 1등이 되어 화면을 소금·설탕으로 채웁니다.
-- 같은 재료의 상품이 여럿이면 싼 것부터 냅니다.

WITH demand AS (
    SELECT ri.ingredient_id,
           COUNT(DISTINCT ri.recipe_id) AS recipe_count
    FROM bubble_recipe_candidate c
    JOIN recipe_ingredient ri ON ri.recipe_id = c.recipe_id
    JOIN ingredient i         ON i.ingredient_id = ri.ingredient_id
    WHERE c.keyword_id = :keyword_id
      AND ri.is_required
      AND NOT i.is_pantry
    GROUP BY ri.ingredient_id
)
SELECT p.product_id,
       p.name,
       p.price,
       p.weight_g,
       p.product_type,
       p.storage_type,
       p.origin_country,
       p.stock_quantity,
       i.ingredient_id,
       i.name AS ingredient_name,
       d.recipe_count
FROM demand d
JOIN ingredient i          ON i.ingredient_id = d.ingredient_id
JOIN product_ingredient pi ON pi.ingredient_id = d.ingredient_id
                          AND pi.role = 'PRIMARY'
JOIN product p             ON p.product_id = pi.product_id
                          AND p.is_active
ORDER BY d.recipe_count DESC,
         p.price ASC,
         p.product_id ASC
LIMIT :max_results
OFFSET :skip;
