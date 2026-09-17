-- name: missing_ingredient_products
-- owner: _template
-- description: 특정 레시피에서 사용자에게 부족한 재료를 채울 상품을 재료별로 추천
-- params: user_id:int, recipe_id:int, max_per_ingredient:int
--
-- 우선순위: 레시피 지정 상품(recipe_product.recommendation_priority) > 최근 인기도 > 낮은 가격.
-- 품절이거나 비활성인 상품은 후보에서 뺍니다.

-- 냉장고는 상품을 담으므로 재료는 상품의 PRIMARY 구성 재료로 풉니다.
-- `fridge_recipe_match` 와 같은 정의를 씁니다. 두 쿼리가 보유 재료를 다르게 보면
-- "부족하다고 했는데 목록에는 없는" 재료가 생깁니다.
WITH fridge AS (
    SELECT DISTINCT pi.ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
),
missing AS (
    SELECT ri.ingredient_id
    FROM recipe_ingredient ri
    JOIN ingredient i  ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN fridge f ON f.ingredient_id = ri.ingredient_id
    WHERE ri.recipe_id = :recipe_id
      AND ri.is_required
      AND f.ingredient_id IS NULL
      AND NOT i.is_pantry
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
    -- PRIMARY 만 봅니다. 냉장고 쪽(위 fridge CTE)이 PRIMARY 로만 보유를 인정하므로,
    -- 여기서 SECONDARY 로 걸린 상품을 추천하면 그걸 담아도 재료는 계속 부족한 채 남습니다.
    -- 밀키트에 조금 들어갔다고 "이걸 사면 두부가 해결된다" 고 말하면 안 됩니다.
    JOIN product_ingredient pi ON pi.ingredient_id = m.ingredient_id
                              AND pi.role = 'PRIMARY'
    JOIN product p             ON p.product_id = pi.product_id
                              AND p.is_active
-- stock_quantity 는 NULL 허용입니다. **NULL 은 품절이 아니라 "수량을 모른다" 입니다**
                              -- (정규화 가이드 4.3: 품절이 명시된 경우만 0 으로 저장).
                              -- COALESCE(..., 0) > 0 으로 거르면 모르는 상품이 전부 품절 취급되는데,
                              -- 지금 적재분은 2,553개가 전부 NULL 이라 결과가 항상 빈손이 됩니다.
                              AND (p.stock_quantity IS NULL OR p.stock_quantity > 0)
    -- recipe_product 의 PK 는 (recipe_id, ingredient_id, product_id) 입니다.
    -- 우선순위가 '어느 재료 자리의 상품인가'까지 포함하므로 ingredient_id 도 조인합니다.
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
