-- name: bubble_candidate_counts
-- owner: openLeeWorld
-- description: 활성 버블별 후보 수. min_candidates 미달이면 화면에 내면 안 됩니다
-- params:
--
-- 버블은 눌렀을 때 결과가 비면 안 됩니다. 데이터가 바뀌면 후보가 줄 수 있어
-- (`전자레인지·에어프라이어` 가 28건이었습니다) 이 쿼리를 pytest 로 고정합니다.
--
-- 규칙 해석은 `bubble_recipe_candidate` 뷰에 있습니다(alembic 0008).
-- 세는 쿼리가 규칙을 따로 들고 있으면, 세기로는 통과하는데 눌렀을 때 다른 목록이
-- 나오는 상태가 됩니다. 실제로 이 파일과 `bubble_recipe_candidates` 가 한동안
-- 서로 다른 해석을 들고 있었습니다.

-- `description` 은 api_spec 13절 응답에 들어갑니다. 화면이 버블 아래 설명으로 씁니다.
SELECT b.keyword_id,
       b.label,
       b.description,
       b.min_candidates,
       COUNT(c.recipe_id) AS candidates,
       COUNT(c.recipe_id) >= b.min_candidates AS is_servable
FROM bubble_keyword b
LEFT JOIN bubble_recipe_candidate c ON c.keyword_id = b.keyword_id
WHERE b.is_active
GROUP BY b.keyword_id, b.label, b.description, b.min_candidates, b.display_order
ORDER BY b.display_order;
