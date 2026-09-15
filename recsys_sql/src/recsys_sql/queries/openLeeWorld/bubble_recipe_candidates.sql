-- name: bubble_recipe_candidates
-- owner: openLeeWorld
-- description: 버블 하나를 눌렀을 때 나올 레시피 후보
-- params: keyword_id:str, max_results:int
--
-- 규칙 해석은 `bubble_recipe_candidate` 뷰에 있습니다(alembic 0008).
-- 버블로 상품도 추천하기 때문에(`bubble_products`) 해석이 두 벌이면 갈라집니다.
-- 임계값과 대상 분류는 여전히 `bubble_keyword.rule_spec` 에 있는 데이터입니다.
--
-- 단계 수가 적은 것을 먼저 냅니다. 조리 단계가 짧으면 화면에서 훑기 쉽습니다.
-- 단계 정보가 없는 레시피(step_count = 0)는 뒤로 보냅니다.

SELECT recipe_id,
       name,
       image_url,
       difficulty,
       prep_time_min,
       cook_time_min,
       servings,
       cooking_method,
       step_count,
       ingredient_count
FROM bubble_recipe_candidate
WHERE keyword_id = :keyword_id
ORDER BY NULLIF(step_count, 0) NULLS LAST,
         recipe_id
LIMIT :max_results;
