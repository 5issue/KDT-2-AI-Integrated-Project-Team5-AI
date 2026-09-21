-- name: fridge_recipe_match
-- owner: _template
-- description: 사용자 냉장고 재료로 만들 수 있는 레시피를 필수 재료 커버리지 순으로 추천
-- params: user_id:int, min_coverage:float, max_results:int
--
-- 상비재료(is_pantry)는 집에 늘 있다고 보고 냉장고에 없어도 보유한 것으로 칩니다.
-- 유통기한이 지난 재료는 제외합니다.
--
-- 냉장고는 재료가 아니라 **상품**을 담습니다. 사용자가 넣는 것은 `한돈 삼겹살 500g` 이지
-- `돼지고기 > 삼겹살` 이 아니기 때문입니다. 재료는 상품의 PRIMARY 구성 재료로 풉니다.
-- PRIMARY 만 보는 이유는 밀키트처럼 구성이 여러 개인 상품에서 "이것도 갖고 있다" 를
-- 넓게 인정하면 커버리지가 부풀기 때문입니다. 부재료까지 보유로 치면 만들 수 없는
-- 레시피가 추천됩니다.
--
-- 냉장고 재료를 하나도 쓰지 않는 레시피는 후보에서 먼저 잘라냅니다. 커버리지를 레시피
-- 전체(1,086건 / 재료 8,393줄)에 대해 구하면 매 요청마다 recipe_ingredient 를 통째로
-- 훑게 되고, 실행계획 검사(`FORBID_SEQ_SCAN_ON`)에도 걸립니다. 잘라낸 레시피는 어차피
-- 커버리지 0 이라 추천 목록에 의미가 없습니다.
-- 다만 필수 재료가 전부 상비재료인 레시피(소금·설탕만 쓰는 것 등)도 함께 빠집니다.
-- "냉장고가 비어도 만들 수 있는 요리" 가 필요하면 그건 별도 쿼리로 다룹니다.

WITH fridge AS (
    -- 냉장고 상품의 PRIMARY 재료와 그 부모(1단계). 계층은 1단계까지만 둡니다.
    SELECT pi.ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
    UNION
    SELECT i.parent_ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    JOIN ingredient i          ON i.ingredient_id = pi.ingredient_id
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
      AND i.parent_ingredient_id IS NOT NULL
),
candidate AS (
    SELECT DISTINCT ri.recipe_id
    FROM fridge f
    JOIN recipe_ingredient ri ON ri.ingredient_id = f.ingredient_id
    WHERE ri.is_required
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
    FROM candidate c
    JOIN recipe_ingredient ri ON ri.recipe_id = c.recipe_id
    JOIN ingredient i         ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN fridge f        ON f.ingredient_id = ri.ingredient_id
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
