-- name: recipe_missing_ingredients
-- owner: openLeeWorld
-- description: 레시피의 재료 중 장바구니 상품과 냉장고로도 채워지지 않는 것
-- params: recipe_id:int, base_product_id:int, user_id:int
--
-- `GET /recipes/{recipeId}/missing-ingredients` 용입니다.
--
-- 보유 판정은 세 갈래입니다.
--   1. 지금 고른 상품(`base_product_id`)의 PRIMARY 재료 -> BASE
--   2. 냉장고에 기한이 남아 있는 상품의 PRIMARY 재료 -> IN_FRIDGE
--   3. 상비재료 -> PANTRY
-- 셋 다 아니면 MISSING 입니다.
--
-- 상태를 하나로 합치지 않고 어디서 채워졌는지까지 냅니다. "이미 담은 상품으로 해결됨"
-- 과 "집에 있음" 은 화면에서 다르게 보여야 합니다.
--
-- `base_product_id` 는 0 을, `user_id` 는 0 을 넘기면 그 갈래를 쓰지 않습니다.
-- 로그인하지 않은 사용자도 이 화면을 볼 수 있어야 합니다.
-- 선택 재료(`is_required = false`)까지 전부 내고, 필수 여부는 컬럼으로 구분합니다.

WITH base AS (
    SELECT pi.ingredient_id
    FROM product_ingredient pi
    WHERE pi.product_id = :base_product_id
      AND pi.role = 'PRIMARY'
),
fridge AS (
    SELECT DISTINCT pi.ingredient_id
    FROM user_fridge uf
    JOIN product_ingredient pi ON pi.product_id = uf.product_id
                              AND pi.role = 'PRIMARY'
    WHERE uf.user_id = :user_id
      AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
)
SELECT ri.recipe_id,
       i.ingredient_id,
       i.name AS ingredient_name,
       ri.quantity,
       ri.unit,
       ri.is_required,
       CASE
           WHEN b.ingredient_id IS NOT NULL THEN 'BASE'
           WHEN f.ingredient_id IS NOT NULL THEN 'IN_FRIDGE'
           WHEN i.is_pantry THEN 'PANTRY'
           ELSE 'MISSING'
       END AS status
FROM recipe_ingredient ri
JOIN ingredient i  ON i.ingredient_id = ri.ingredient_id
LEFT JOIN base b   ON b.ingredient_id = ri.ingredient_id
LEFT JOIN fridge f ON f.ingredient_id = ri.ingredient_id
WHERE ri.recipe_id = :recipe_id
-- 부족한 것부터 냅니다. 이 화면의 목적이 그것입니다.
ORDER BY ri.is_required DESC,
         CASE
             WHEN b.ingredient_id IS NOT NULL THEN 3
             WHEN f.ingredient_id IS NOT NULL THEN 2
             WHEN i.is_pantry THEN 4
             ELSE 1
         END,
         i.name;
