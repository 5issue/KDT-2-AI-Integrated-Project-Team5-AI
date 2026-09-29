-- name: user_recent_recipes
-- owner: chaeyeon089
-- description: 최근 본 레시피 목록. 마지막으로 본 순으로 카드에 필요한 컬럼만 냅니다
-- params: user_id:int, max_results:int
--
-- `GET /users/me/recent-recipes` 용입니다.
--
-- `user_recipe_view` 는 (user_id, recipe_id) 한 쌍이 한 행이고 다시 보면 `viewed_at` 만
-- 당겨지므로, 여기서 DISTINCT 없이 정렬만 하면 같은 레시피가 두 번 나오지 않습니다.
-- 행 상한은 쓰기 쪽(서빙 `TRIM_VIEWS`)이 지키고, 읽기는 `max_results` 로만 자릅니다.

SELECT v.recipe_id,
       r.name,
       r.image_url,
       r.difficulty,
       r.cook_time_min,
       r.servings,
       v.viewed_at
FROM user_recipe_view v
JOIN recipe r ON r.recipe_id = v.recipe_id
WHERE v.user_id = :user_id
ORDER BY v.viewed_at DESC, v.recipe_id DESC
LIMIT :max_results;
