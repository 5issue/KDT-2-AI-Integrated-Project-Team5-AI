-- name: my_fridge_items
-- owner: openLeeWorld
-- description: 마이냉장고 목록. 상품과 그 대표 재료를 함께 냅니다
-- params: user_id:int
--
-- `GET /users/me/fridge` 용입니다.
--
-- 냉장고는 상품을 담고, 화면은 재료 이름도 함께 보여 줍니다(api_spec 20절).
-- 재료는 `product_ingredient` 의 PRIMARY 로 풉니다. 밀키트처럼 PRIMARY 가 여럿인
-- 상품은 여러 줄이 아니라 배열 하나로 냅니다. 냉장고 한 칸은 한 줄이어야 합니다.
--
-- api_spec 의 `fridge_item_id` 에 해당하는 컬럼이 없습니다. `user_fridge` 의 키가
-- (user_id, product_id) 복합키라 단일 식별자가 없습니다. 화면이 수정·삭제에 쓸 키로
-- product_id 를 그대로 내보냅니다. 대리키가 필요하면 스키마에 먼저 추가해야 합니다.
--
-- 기한이 지난 것도 냅니다. 사용자가 치우려면 보여야 합니다. `is_expired` 로 구분합니다.

SELECT uf.user_id,
       uf.product_id,
       p.name AS product_name,
       p.storage_type,
       p.weight_g,
       uf.quantity,
       uf.unit,
       uf.expires_at,
       uf.expires_at IS NOT NULL AND uf.expires_at < NOW() AS is_expired,
       COALESCE(
           JSONB_AGG(
               JSONB_BUILD_OBJECT('ingredient_id', i.ingredient_id, 'name', i.name)
               ORDER BY i.name
           ) FILTER (WHERE i.ingredient_id IS NOT NULL),
           '[]'::jsonb
       ) AS ingredients
FROM user_fridge uf
JOIN product p                  ON p.product_id = uf.product_id
LEFT JOIN product_ingredient pi ON pi.product_id = uf.product_id
                               AND pi.role = 'PRIMARY'
LEFT JOIN ingredient i          ON i.ingredient_id = pi.ingredient_id
WHERE uf.user_id = :user_id
GROUP BY uf.user_id, uf.product_id, p.name, p.storage_type, p.weight_g,
         uf.quantity, uf.unit, uf.expires_at
ORDER BY uf.expires_at NULLS LAST, p.name;
