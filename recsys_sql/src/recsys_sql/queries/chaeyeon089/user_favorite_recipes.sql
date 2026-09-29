-- name: user_favorite_recipes
-- owner: chaeyeon089
-- description: 찜한 레시피 목록. 최근에 찜한 순으로 카드에 필요한 컬럼만 냅니다
-- params: user_id:int, max_results:int
--
-- `GET /users/me/favorite-recipes` 용입니다.
--
-- 화면은 가로 스크롤 카드(이미지 + 이름)라 상세 컬럼은 내지 않습니다. 재료줄·조리 단계는
-- 카드를 눌렀을 때 `recipe_detail` 이 냅니다.
--
-- 찜을 취소하면 행이 지워지므로 별도 상태 컬럼이 없습니다. 레시피가 삭제되면 FK CASCADE 로
-- 같이 사라져 "이름 없는 카드" 가 생기지 않습니다.

SELECT f.recipe_id,
       r.name,
       r.image_url,
       r.difficulty,
       r.cook_time_min,
       r.servings,
       f.created_at AS favorited_at
FROM user_recipe_favorite f
JOIN recipe r ON r.recipe_id = f.recipe_id
WHERE f.user_id = :user_id
ORDER BY f.created_at DESC, f.recipe_id DESC
LIMIT :max_results;
