-- name: my_recipe_candidates
-- owner: openLeeWorld
-- description: 마이냉장고 기반 레시피 추천. 부족·보유·상비 재료 목록까지 함께 냅니다
-- params: user_id:int, min_match_rate:float, max_results:int
--
-- `GET /recommendations/my-recipes` 용입니다.
--
-- `_template/fridge_recipe_match` 와 판정 규칙은 같습니다. 다른 점은 응답 모양입니다.
-- api_spec 21절이 부족 재료 **목록**까지 요구해서, 개수만 내는 쪽으로는 부족합니다.
-- 화면에서 개수를 보고 다시 재료를 물으면 왕복이 레시피 수만큼 늘어납니다.
--
-- 보유 판정: 냉장고 상품의 PRIMARY 재료와 그 부모 계층 + 상비재료.
-- Product가 목심처럼 구체적인 child를 가리키면 돼지고기 같은 상위 Recipe 재료도
-- 채웁니다. 반대 방향(돼지고기 보유로 목심 충족)은 허용하지 않습니다.
-- 냉장고 재료를 하나도 쓰지 않는 레시피는 후보에서 먼저 잘라냅니다.
--
-- 정렬: 레시피별 추천 상품 우선순위(`recipe_product.recommendation_priority` 의 MAX) 가
-- 첫 기준입니다. 데모·운영이 밀고 싶은 레시피는 seed 가 이 값을 넣고, SQL 은 레시피 id 를
-- 모릅니다. 우선순위가 같으면 매칭률 -> 부족 수 -> 조리시간 -> id 순입니다.
-- 우선순위는 레시피당 한 번 집계합니다. recipe_product 를 그대로 조인하면 상품 수만큼
-- 행이 불어납니다.
--
-- 세 목록(missing / held / pantry)은 추천 이유 생성(`rag_lab.reason_service`)의 입력입니다.
-- 생성 서비스는 `len(held) + len(pantry) == available_count`, `len(missing) == missing_count`
-- 불변식을 검사하고 어긋나면 LLM 을 부르지 않습니다. 그래서 상비재료는 냉장고에 있어도
-- pantry 에만 넣고 held 에서는 뺍니다. 세 목록은 서로 겹치지 않습니다.

WITH fridge AS (
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
candidate AS (
    SELECT DISTINCT ri.recipe_id
    FROM fridge f
    JOIN recipe_ingredient ri ON ri.ingredient_id = f.ingredient_id
    WHERE ri.is_required
),
match AS (
    SELECT ri.recipe_id,
           COUNT(*) FILTER (WHERE ri.is_required) AS required_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND (f.ingredient_id IS NOT NULL OR i.is_pantry)
           ) AS available_count,
           COUNT(*) FILTER (
               WHERE ri.is_required AND f.ingredient_id IS NULL AND NOT i.is_pantry
           ) AS missing_count,
           COALESCE(
               JSONB_AGG(
                   JSONB_BUILD_OBJECT('ingredient_id', i.ingredient_id, 'name', i.name)
                   ORDER BY i.name
               ) FILTER (
                   WHERE ri.is_required AND f.ingredient_id IS NULL AND NOT i.is_pantry
               ),
               '[]'::jsonb
           ) AS missing_ingredients,
           -- 냉장고 보유 (상비재료 제외). 추천 이유 첫 문장의 근거입니다.
           COALESCE(
               JSONB_AGG(
                   JSONB_BUILD_OBJECT('ingredient_id', i.ingredient_id, 'name', i.name)
                   ORDER BY i.name
               ) FILTER (
                   WHERE ri.is_required AND f.ingredient_id IS NOT NULL AND NOT i.is_pantry
               ),
               '[]'::jsonb
           ) AS held_ingredients,
           -- 상비재료. 집에 있다고 보고 보유로 치되, 사라고 하면 안 되는 것.
           COALESCE(
               JSONB_AGG(
                   JSONB_BUILD_OBJECT('ingredient_id', i.ingredient_id, 'name', i.name)
                   ORDER BY i.name
               ) FILTER (
                   WHERE ri.is_required AND i.is_pantry
               ),
               '[]'::jsonb
           ) AS pantry_ingredients
    FROM candidate c
    JOIN recipe_ingredient ri ON ri.recipe_id = c.recipe_id
    JOIN ingredient i         ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN fridge f        ON f.ingredient_id = ri.ingredient_id
    GROUP BY ri.recipe_id
),
priority AS (
    SELECT rp.recipe_id,
           MAX(rp.recommendation_priority) AS recommendation_priority
    FROM recipe_product rp
    GROUP BY rp.recipe_id
)
SELECT r.recipe_id,
       r.name,
       r.image_url,
       r.difficulty,
       r.cook_time_min,
       r.servings,
       m.required_count,
       m.available_count,
       m.missing_count,
       ROUND(m.available_count::numeric / m.required_count, 3) AS match_rate,
       m.missing_ingredients,
       m.held_ingredients,
       m.pantry_ingredients
FROM match m
JOIN recipe r          ON r.recipe_id = m.recipe_id
LEFT JOIN priority pr  ON pr.recipe_id = m.recipe_id
WHERE m.required_count > 0
  AND m.available_count::numeric / m.required_count >= :min_match_rate
ORDER BY COALESCE(pr.recommendation_priority, 0) DESC,
         match_rate DESC,
         m.missing_count ASC,
         r.cook_time_min ASC NULLS LAST,
         r.recipe_id ASC
LIMIT :max_results;
