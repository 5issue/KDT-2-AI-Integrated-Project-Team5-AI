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
--   (부위에 지침이 없으면 ingredient.parent_ingredient_id 의 지침)
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
--
-- ## 같은 (장소, 상황)이 여러 줄인 경우
--
-- FoodKeeper 는 한 재료를 여러 갈래로 나눠 둡니다. `햄` 하나에 bone-in/boneless,
-- whole/half, fully-cooked/cook-before-eating 이 따로 있어 같은 재료에 19줄이 붙습니다.
-- 그대로 내보내면 화면에 `냉장 · 구매후` 가 19번 찍힙니다.
--
-- **기간이 다를 때는 짧은 쪽을 냅니다.** 지금 적재분에서 중복 160조 중 125조가 기간이
-- 어긋나고, `게류 냉장 구매후` 는 `10-12개월` 과 `2-4 일` 이 함께 있습니다. 긴 쪽을
-- 보여 주면 상한 음식을 먹으라고 하는 셈입니다. 고를 수 없으면 짧은 쪽이 안전합니다.
-- 단위가 섞여 있어 일 단위로 환산해 비교합니다.
--
-- 이건 조회 단계의 방어입니다. 중복 자체는 적재에서 정리해야 하고,
-- 규칙은 `docs/product-ingredient-storage-normalization-guide.md` 5.6 절에 있습니다.

SELECT DISTINCT ON (sg.storage_location, sg.storage_context)
       p.product_id,
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
-- 부위(child)에 지침이 없으면 부모 지침을 씁니다. `목심` 에 없으면 `돼지고기` 지침입니다.
-- 부위 자기 지침이 하나라도 있으면 부모 지침과 섞지 않습니다. 이때 상품 보관 장소와 같은 지침만 셉니다.
-- 부위에 `냉동` 지침만 있고 상품이 `냉장` 이면 부모의 `냉장` 지침으로 넘어가야 404 가 나지 않습니다.
JOIN storage_guideline sg  ON sg.ingredient_id = CASE
         WHEN EXISTS (
             SELECT 1 FROM storage_guideline own
             WHERE own.ingredient_id = i.ingredient_id
               AND (p.storage_type IS NULL OR own.storage_location = p.storage_type)
         )
         THEN i.ingredient_id
         ELSE COALESCE(i.parent_ingredient_id, i.ingredient_id)
     END
WHERE p.product_id = :product_id
  AND (p.storage_type IS NULL OR sg.storage_location = p.storage_type)
  -- PRIMARY 가 둘 이상인 상품은 통째로 제외합니다.
  AND (
      SELECT COUNT(*)
      FROM product_ingredient pick
      WHERE pick.product_id = p.product_id
        AND pick.role = 'PRIMARY'
  ) = 1
-- DISTINCT ON 은 ORDER BY 앞부분이 그룹 키와 같아야 합니다.
ORDER BY sg.storage_location,
         sg.storage_context,
         -- 기간이 짧은 것부터. 단위가 섞여 있어 일로 환산합니다.
         COALESCE(sg.duration_max, sg.duration_min) * CASE sg.duration_unit
             WHEN '시간' THEN 1.0 / 24
             WHEN '일'   THEN 1
             WHEN '주'   THEN 7
             WHEN '개월' THEN 30
             WHEN '년'   THEN 365
             ELSE 1
         END ASC NULLS LAST,
         -- 기간이 같으면 팁이 있는 쪽을 먼저(정규화 가이드 5.6 절).
         (sg.storage_tips IS NOT NULL) DESC,
         sg.storage_id;
