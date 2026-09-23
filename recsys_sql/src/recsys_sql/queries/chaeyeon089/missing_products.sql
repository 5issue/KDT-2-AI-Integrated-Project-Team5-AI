-- name: missing_products
-- owner: chaeyeon089
-- description: 부족 재료와 재료별 추천 상품. 기준 상품(base_product_id) 갈래 포함
-- params: user_id:int, recipe_id:int, base_product_id:int, max_per_ingredient:int
--
-- `GET /recipes/{recipeId}/missing-products` 용입니다. api_spec 18장(부족 재료 계산)과
-- 30장(부족 재료 상품 추천)을 하나로 통일한 명세를 받칩니다.
--
-- `missing_ingredient_products` 에 base 갈래를 더한 것입니다. 기존 18장의
-- "지금 고른 상품(base_product_id)이 채우는 재료는 부족 목록에서 제외" 를 유지해,
-- 방금 담은 상품을 다시 추천하는 일이 없게 합니다.
--
-- 보유 판정은 fridge_recipe_match / my_recipe_candidates 와 같은 정의를 씁니다
-- (냉장고 상품의 PRIMARY 재료와 부모 계층 + 상비재료). 다르면 "추천에서 부족하다던 재료가
-- 여기에는 없는" 불일치가 생깁니다.
--
-- user_id 0 은 냉장고 갈래 미사용(비로그인), base_product_id 0 은 기준 상품 미사용입니다.
-- 상품 랭킹: 레시피 지정 상품(recipe_product) > 최근 인기도 > 낮은 가격.
-- 비활성/품절 제외. stock_quantity NULL 은 품절이 아니라 "수량 미상"이라 후보에 남깁니다.

WITH base AS (
    -- 기준 상품의 PRIMARY 재료와 그 부모(1단계).
    -- 한 번 읽고 행마다 (자기 재료, 부모 재료) 두 값을 펼칩니다. 부모가 없으면 NULL 이라 거릅니다.
    SELECT DISTINCT h.ingredient_id
    FROM product_ingredient pi
    LEFT JOIN ingredient i ON i.ingredient_id = pi.ingredient_id
    CROSS JOIN LATERAL (VALUES (pi.ingredient_id), (i.parent_ingredient_id)) AS h(ingredient_id)
    WHERE pi.product_id = :base_product_id
      AND pi.role = 'PRIMARY'
      AND h.ingredient_id IS NOT NULL
),
fridge AS (
    -- 냉장고 상품의 PRIMARY 재료와 그 부모(1단계). 계층은 1단계까지만 둡니다.
    -- 한 번 읽고 행마다 (자기 재료, 부모 재료) 두 값을 펼칩니다. 부모가 없으면 NULL 이라 거릅니다.
    SELECT DISTINCT h.ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    LEFT JOIN ingredient i     ON i.ingredient_id = pi.ingredient_id
    CROSS JOIN LATERAL (VALUES (pi.ingredient_id), (i.parent_ingredient_id)) AS h(ingredient_id)
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
      AND h.ingredient_id IS NOT NULL
),
missing AS (
    SELECT ri.ingredient_id
    FROM recipe_ingredient ri
    JOIN ingredient i  ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN fridge f ON f.ingredient_id = ri.ingredient_id
    LEFT JOIN base b   ON b.ingredient_id = ri.ingredient_id
    WHERE ri.recipe_id = :recipe_id
      AND ri.is_required
      AND f.ingredient_id IS NULL
      AND b.ingredient_id IS NULL
      AND NOT i.is_pantry
),
product_coverage AS (
    -- 상품이 채우는 재료: PRIMARY 재료와 그 부모(1단계). 목심 상품은 돼지고기 요구도 채웁니다.
    -- 한 번 읽고 행마다 (자기 재료, 부모 재료) 두 값을 펼칩니다. 부모가 없으면 NULL 이라 거릅니다.
    SELECT DISTINCT pi.product_id,
           h.ingredient_id
    FROM product_ingredient pi
    JOIN product p         ON p.product_id = pi.product_id
                          AND p.is_active
                          AND (p.stock_quantity IS NULL OR p.stock_quantity > 0)
    LEFT JOIN ingredient i ON i.ingredient_id = pi.ingredient_id
    CROSS JOIN LATERAL (VALUES (pi.ingredient_id), (i.parent_ingredient_id)) AS h(ingredient_id)
    WHERE pi.role = 'PRIMARY'
      AND h.ingredient_id IS NOT NULL
      AND h.ingredient_id IN (SELECT ingredient_id FROM missing)
),
ranked AS (
    SELECT m.ingredient_id,
           p.product_id,
           p.name AS product_name,
           p.price,
           COALESCE(rp.recommendation_priority, 0) AS recommendation_priority,
           COALESCE(pop.popularity_score, 0)       AS popularity_score,
           ROW_NUMBER() OVER (
               PARTITION BY m.ingredient_id
               ORDER BY COALESCE(rp.recommendation_priority, 0) DESC,
                        COALESCE(pop.popularity_score, 0) DESC,
                        p.price ASC,
                        p.product_id ASC
           ) AS rank_in_ingredient
    FROM missing m
    -- PRIMARY 만 봅니다. 보유 판정이 PRIMARY 기준이므로, SECONDARY 로 걸린 상품을
    -- 추천하면 그걸 담아도 재료는 계속 부족한 채 남습니다.
    JOIN product_coverage pc   ON pc.ingredient_id = m.ingredient_id
    JOIN product p             ON p.product_id = pc.product_id
    LEFT JOIN recipe_product rp ON rp.recipe_id = :recipe_id
                               AND rp.product_id = p.product_id
                               AND rp.ingredient_id = m.ingredient_id
    LEFT JOIN LATERAL (
        SELECT pp.popularity_score
        FROM product_popularity pp
        WHERE pp.product_id = p.product_id
        ORDER BY pp.period_end DESC
        LIMIT 1
    ) pop ON TRUE
)
SELECT i.ingredient_id,
       i.name AS ingredient_name,
       r.product_id,
       r.product_name,
       r.price,
       r.recommendation_priority,
       r.popularity_score,
       r.rank_in_ingredient
FROM ranked r
JOIN ingredient i ON i.ingredient_id = r.ingredient_id
WHERE r.rank_in_ingredient <= :max_per_ingredient
ORDER BY i.ingredient_id, r.rank_in_ingredient;
