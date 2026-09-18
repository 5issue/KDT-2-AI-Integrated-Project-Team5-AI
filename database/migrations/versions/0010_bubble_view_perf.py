"""bubble_recipe_candidate 뷰를 레시피당 한 번만 집계하도록

0008 의 뷰는 (버블 x 레시피) 쌍마다 재료 집계를 다시 돌렸다. 버블 5개 x 레시피 1,086개 =
5,430번이다. 안쪽에서 `ingredient` 를 41,965번, `category` 를 27,725번 인덱스 조회한다.

홈 화면 첫 쿼리(`bubble_candidate_counts`)를 `EXPLAIN (ANALYZE, BUFFERS)` 로 재보면
5행을 내는 데 **버퍼 232,931 블록 / 335ms** 였다. Neon 은 계산 노드와 스토리지가 분리돼
있어, 캐시가 식으면 이 블록들이 그대로 네트워크 왕복이 된다.

집계가 버블에 의존하지 않는다는 것이 핵심이다. 재료 수도 단계 수도 레시피만의 값이고,
버블마다 다른 것은 "어느 분류를 세느냐" 뿐이다. 그래서 레시피당 한 번
`{분류: 개수}` jsonb 를 만들어 두고, 버블 규칙은 그 맵에서 필요한 분류만 더한다.
테이블을 다시 읽지 않는다.

    bubble_candidate_counts    232,931 블록 335ms  ->    416 블록  30ms
    bubble_recipe_candidates    46,698 블록  63ms  ->    415 블록  15ms
    bubble_products             49,645 블록  73ms  ->  5,375 블록  27ms

    (버퍼 수치는 EXPLAIN 루트 노드 기준이다. PostgreSQL 은 상위 노드에 하위 값을 누적해
     보고하므로 루트가 곧 트리 전체의 합이고, 노드를 재귀로 더하면 중복으로 세어진다.)

결과 집합은 같다. 버블 5종의 후보 수가 변경 전후로 동일한 것을 확인했고,
`recsys_sql/tests/test_bubble_queries.py` 와 `test_query_grain.py` 가 고정한다.

Revision ID: 0010_bubble_view_perf
Revises: 0009_cooking_method
"""

from alembic import op

revision = "0010_bubble_view_perf"
down_revision = "0009_cooking_method"
branch_labels = None
depends_on = None

VIEW = """
CREATE OR REPLACE VIEW bubble_recipe_candidate AS
WITH per_category AS (
    SELECT ri.recipe_id, cat.name AS category_name, COUNT(*) AS n
    FROM recipe_ingredient ri
    JOIN ingredient i      ON i.ingredient_id = ri.ingredient_id
    LEFT JOIN category cat ON cat.category_id = i.ingredient_category_id
    WHERE NOT i.is_pantry
    GROUP BY ri.recipe_id, cat.name
),
per_recipe AS (
    SELECT recipe_id,
           SUM(n)::bigint AS total,
           COALESCE(JSONB_OBJECT_AGG(category_name, n) FILTER (WHERE category_name IS NOT NULL), '{}'::jsonb)
               AS by_category
    FROM per_category
    GROUP BY recipe_id
),
per_recipe_steps AS (
    SELECT recipe_id, COUNT(*) AS steps FROM recipe_step GROUP BY recipe_id
)
SELECT b.keyword_id, b.min_candidates, r.recipe_id, r.name, r.image_url, r.difficulty,
       r.prep_time_min, r.cook_time_min, r.servings, r.cooking_method,
       COALESCE(s.steps, 0) AS step_count,
       COALESCE(g.total, 0) AS ingredient_count
FROM bubble_keyword b
CROSS JOIN recipe r
LEFT JOIN per_recipe g       ON g.recipe_id = r.recipe_id
LEFT JOIN per_recipe_steps s ON s.recipe_id = r.recipe_id
CROSS JOIN LATERAL (
    SELECT COALESCE(SUM((g.by_category ->> wanted.name)::int), 0) AS in_category
    FROM jsonb_array_elements_text(COALESCE(b.rule_spec -> 'category_names', '[]'::jsonb)) AS wanted(name)
) hit
WHERE b.is_active
  AND CASE b.rule_type
        WHEN 'ingredient_count' THEN g.total > 0 AND g.total <= (b.rule_spec ->> 'value')::int
        WHEN 'step_count' THEN s.steps > 0 AND s.steps <= (b.rule_spec ->> 'value')::int
        WHEN 'ingredient_category' THEN
            CASE b.rule_spec ->> 'op'
                WHEN 'ratio_gte' THEN g.total > 0
                    AND hit.in_category::float / g.total >= (b.rule_spec ->> 'value')::float
                ELSE hit.in_category > 0
            END
        WHEN 'cook_time' THEN r.cook_time_min IS NOT NULL AND r.cook_time_min <= (b.rule_spec ->> 'value')::int
        WHEN 'servings' THEN r.servings IS NOT NULL AND r.servings <= (b.rule_spec ->> 'value')::numeric
        ELSE FALSE
      END
"""

PREVIOUS_VIEW = """
CREATE OR REPLACE VIEW bubble_recipe_candidate AS
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
    op.execute(
        "COMMENT ON VIEW bubble_recipe_candidate IS "
        "'버블별 후보 레시피. rule_spec 해석을 여기 한 곳에 둔다. 집계는 레시피당 한 번만 한다.'"
    )


def downgrade() -> None:
    op.execute(PREVIOUS_VIEW)
