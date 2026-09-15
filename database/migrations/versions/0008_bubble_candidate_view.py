"""bubble_recipe_candidate 뷰 - 버블 규칙 해석을 한 곳에

버블 규칙(`bubble_keyword.rule_spec`)을 읽어 후보 레시피를 내놓는 일을 뷰 하나로 모은다.

버블을 쓰는 화면이 둘이다. 버블을 누르면 레시피 목록이 나오고(`/recommendations/...`),
같은 버블로 상품도 추천한다. 두 쿼리가 규칙 해석을 각자 들고 있으면 언젠가 갈라지고,
그때 "버블에서 본 상품으로 만들 수 있는 레시피가 그 버블에 없는" 상태가 된다.
해석은 한 벌이어야 한다.

규칙 자체는 여전히 데이터다. 뷰는 `rule_type` 별 해석 방법만 알고,
임계값과 대상 분류는 `rule_spec` 에서 읽는다. 버블을 갈아끼울 때 뷰를 고치지 않는다.

새 `rule_type` 을 쓰려면 여기를 고쳐야 한다. `0005_bubble_keyword` 의 CHECK 에는
`seasonal`, `popularity`, `text_search` 도 있지만 해석이 아직 없다.
해석이 없는 rule_type 은 후보를 0건으로 내므로, `min_candidates` 검증에서 걸린다.

Revision ID: 0008_bubble_candidate_view
Revises: 0007_korean_storage_enums
"""

from alembic import op

revision = "0008_bubble_candidate_view"
down_revision = "0007_korean_storage_enums"
branch_labels = None
depends_on = None

VIEW = """
CREATE VIEW bubble_recipe_candidate AS
SELECT b.keyword_id,
       b.min_candidates,
       r.recipe_id,
       r.name,
       r.image_url,
       r.difficulty,
       r.prep_time_min,
       r.cook_time_min,
       r.servings,
       r.cooking_method,
       COALESCE(s.steps, 0) AS step_count,
       COALESCE(g.total, 0) AS ingredient_count
FROM bubble_keyword b
CROSS JOIN recipe r
LEFT JOIN LATERAL (
    -- 상비재료를 뺀 재료 집계. 소금·설탕까지 세면 '재료 적게 드는 요리' 에
    -- 걸릴 레시피가 거의 없다(재료줄의 34%가 상비재료).
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (
               WHERE cat.name = ANY (
                   ARRAY(SELECT jsonb_array_elements_text(b.rule_spec -> 'category_names'))
               )
           ) AS in_category
    FROM recipe_ingredient ri
    JOIN ingredient i       ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN category cat  ON cat.category_id = i.ingredient_category_id
    WHERE ri.recipe_id = r.recipe_id
      AND NOT i.is_pantry
) g ON TRUE
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS steps
    FROM recipe_step rs
    WHERE rs.recipe_id = r.recipe_id
) s ON TRUE
WHERE b.is_active
  AND CASE b.rule_type
        WHEN 'ingredient_count' THEN
            g.total > 0 AND g.total <= (b.rule_spec ->> 'value')::int
        WHEN 'step_count' THEN
            s.steps > 0 AND s.steps <= (b.rule_spec ->> 'value')::int
        WHEN 'ingredient_category' THEN
            CASE b.rule_spec ->> 'op'
                WHEN 'ratio_gte' THEN
                    g.total > 0
                    AND g.in_category::float / g.total >= (b.rule_spec ->> 'value')::float
                ELSE g.in_category > 0
            END
        WHEN 'cook_time' THEN
            r.cook_time_min IS NOT NULL
            AND r.cook_time_min <= (b.rule_spec ->> 'value')::int
        WHEN 'servings' THEN
            r.servings IS NOT NULL
            AND r.servings <= (b.rule_spec ->> 'value')::numeric
        ELSE FALSE
      END
"""


def upgrade() -> None:
    op.execute(VIEW)
    op.execute("COMMENT ON VIEW bubble_recipe_candidate IS '버블별 후보 레시피. rule_spec 해석을 여기 한 곳에 둔다.'")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS bubble_recipe_candidate")
