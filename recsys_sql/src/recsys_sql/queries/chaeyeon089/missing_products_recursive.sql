-- name: missing_products_recursive
-- owner: chaeyeon089
-- description: [후보] missing_products 의 재귀 CTE 버전. 서빙은 쓰지 않고 비교용으로 보존합니다
-- params: user_id:int, recipe_id:int, base_product_id:int, max_per_ingredient:int
--
-- 서비스가 쓰는 `missing_products` 는 계층을 1단계(자기 재료 + 부모)까지만 봅니다. 이 파일은 같은
-- 규칙을 `WITH RECURSIVE` 로 조상 끝까지 따라가는 원래 초안입니다. 결과 모양과 랭킹은
-- `missing_products` 와 같고, 다른 곳은 base·fridge·product_coverage 세 CTE 뿐입니다.
--
-- `missing_products` 를 1단계로 둔 이유: 현재 데이터의 계층 깊이는 최대 1단계(손자 재료 0)라 두 쿼리의
-- 결과가 같습니다. 손자 재료가 생기면 결과가 갈리고, 그때 이 후보로 바꿀지 판단합니다.
-- 성능 비교는 PR #28 의 EXPLAIN ANALYZE 결과를 봅니다.
--
-- 안전장치:
--   - `CYCLE ingredient_id SET is_cycle USING path` 로 A -> B -> A 같은 순환을 만나면
--     그 경로의 재귀를 멈춥니다. 데이터가 잘못 들어와도 무한 루프에 빠지지 않습니다.
--   - UNION ALL 이라 같은 재료가 여러 경로로 나올 수 있어, 쓰는 쪽 CTE 에서 DISTINCT 로 접습니다.
--   - 재귀 한 단계가 `ingredient.ingredient_id`(PK) 로 조인하므로 부모 쪽으로 올라갈 때는
--     `parent_ingredient_id` 인덱스가 필요 없습니다.

WITH RECURSIVE base_walk(ingredient_id) AS (
    -- 기준 상품의 PRIMARY 재료에서 시작해 조상을 끝까지 따라갑니다.
    SELECT pi.ingredient_id
    FROM product_ingredient pi
    WHERE pi.product_id = :base_product_id
      AND pi.role = 'PRIMARY'
    UNION ALL
    SELECT i.parent_ingredient_id
    FROM base_walk b
    JOIN ingredient i ON i.ingredient_id = b.ingredient_id
    WHERE i.parent_ingredient_id IS NOT NULL
) CYCLE ingredient_id SET is_cycle USING path,
base AS (
    SELECT DISTINCT ingredient_id FROM base_walk WHERE NOT is_cycle
),
fridge_walk(ingredient_id) AS (
    -- 냉장고 상품의 PRIMARY 재료에서 시작해 조상을 끝까지 따라갑니다.
    SELECT pi.ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
    UNION ALL
    SELECT i.parent_ingredient_id
    FROM fridge_walk f
    JOIN ingredient i ON i.ingredient_id = f.ingredient_id
    WHERE i.parent_ingredient_id IS NOT NULL
) CYCLE ingredient_id SET is_cycle USING path,
fridge AS (
    SELECT DISTINCT ingredient_id FROM fridge_walk WHERE NOT is_cycle
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
coverage_walk(product_id, ingredient_id) AS (
    -- 상품이 채우는 재료: PRIMARY 재료와 그 조상 전부. 목심 상품은 돼지고기 요구도 채웁니다.
    SELECT pi.product_id,
           pi.ingredient_id
    FROM product_ingredient pi
    JOIN product p ON p.product_id = pi.product_id
                  AND p.is_active
                  AND (p.stock_quantity IS NULL OR p.stock_quantity > 0)
    WHERE pi.role = 'PRIMARY'
    UNION ALL
    SELECT c.product_id,
           i.parent_ingredient_id
    FROM coverage_walk c
    JOIN ingredient i ON i.ingredient_id = c.ingredient_id
    WHERE i.parent_ingredient_id IS NOT NULL
) CYCLE ingredient_id SET is_cycle USING path,
product_coverage AS (
    SELECT DISTINCT product_id, ingredient_id FROM coverage_walk WHERE NOT is_cycle
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
