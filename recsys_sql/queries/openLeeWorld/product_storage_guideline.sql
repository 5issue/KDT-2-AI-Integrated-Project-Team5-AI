-- name: product_storage_guideline
-- owner: openLeeWorld
-- description: 상품 하나의 보관법. PRIMARY 재료가 정확히 하나일 때만 알려줍니다
-- params: product_id:int
--
-- 보관법은 상품이 아니라 재료에 붙어 있습니다. FoodKeeper 가 SKU 가 아니라 식재료 단위로
-- 지침을 주기 때문입니다. 같은 `돼지고기 > 삼겹살` 을 파는 상품이 몇 개든 지침은 하나면
-- 됩니다. 상품마다 복사해 두면 원문이 정정될 때 전부 고쳐야 합니다.
--
-- 조회 경로:
--   product -> product_ingredient(role = PRIMARY) -> ingredient -> storage_guideline
--
-- **PRIMARY 재료가 둘 이상이면 아무것도 내지 않습니다.** 밀키트나 양념육처럼 구성이 여러
-- 개인 상품에 단일 보관법을 붙이는 것은 논리적으로 맞지 않습니다. 지금 적재분에서 상품
-- 2,386개 중 36개가 여기 해당합니다. 틀린 보관법을 보여 주느니 안 보여 주는 편이 낫습니다.
--
-- 장소는 `product.storage_type`(판매 시점 기본 장소)으로 거릅니다. 사용자가 냉장 상품을
-- 냉동실에 넣었다면 그 장소가 우선이어야 하는데, 그 값을 담을
-- `user_fridge.storage_location` 은 아직 없습니다(PR #10 에서 들어옵니다).
-- 들어오면 여기 COALESCE 한 줄을 얹습니다.
--
-- 기본 장소가 비어 있는 상품(1,551건)은 장소로 거르지 않고 지침 전부를 돌려줍니다.
-- 상황(일반/구매후/개봉후/해동후)이 여러 개면 하나를 임의로 고르지 않고 목록으로 냅니다.
-- 어느 쪽을 보여 줄지는 화면이 정할 일입니다.

SELECT p.product_id,
       p.name AS product_name,
       p.storage_type,
       i.ingredient_id,
       i.name AS ingredient_name,
       sg.storage_location,
       sg.storage_context,
       sg.duration_min,
       sg.duration_max,
       sg.duration_unit,
       sg.duration_text,
       sg.storage_tips
FROM product p
JOIN product_ingredient pi ON pi.product_id = p.product_id
                          AND pi.role = 'PRIMARY'
JOIN ingredient i          ON i.ingredient_id = pi.ingredient_id
JOIN storage_guideline sg  ON sg.ingredient_id = i.ingredient_id
WHERE p.product_id = :product_id
  AND (p.storage_type IS NULL OR sg.storage_location = p.storage_type)
  -- PRIMARY 가 둘 이상인 상품은 통째로 제외합니다.
  AND (
      SELECT COUNT(*)
      FROM product_ingredient pick
      WHERE pick.product_id = p.product_id
        AND pick.role = 'PRIMARY'
  ) = 1
ORDER BY sg.storage_location,
         sg.storage_context,
         sg.storage_id;
