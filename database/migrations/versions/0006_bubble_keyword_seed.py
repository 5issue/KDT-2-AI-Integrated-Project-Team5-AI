"""bubble_keyword 1차 후보 5종 시드

`ai_context/todo_roadmap.md` 2번의 1차 후보를 넣는다.
후보 수는 실제 적재된 데이터로 재본 값이며, 주석에 남겨 둔다.
데이터가 바뀌면 달라지므로 `recsys_sql` 의 검증 쿼리로 확인한다.

`difficulty` 기반('요리 초보도 실패 없는')은 뺐다. 필드가 25%만 채워져 있어
로드맵이 정한 결측률 조건을 넘지 못한다. 대신 단계 수 기반을 쓴다.

`혼자 먹기 딱 좋은 한 그릇`도 뺐다. `dish_type` 이 스키마에 없어
인분만으로는 '한 그릇'을 가릴 수 없다.

Revision ID: 0006_bubble_keyword_seed
Revises: 0005_bubble_keyword
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_bubble_keyword_seed"
down_revision = "0005_bubble_keyword"
branch_labels = None
depends_on = None

# (keyword_id, label, description, rule_type, rule_spec, min_candidates, display_order)
# 뒤 주석은 2026-09-10 적재분 기준 후보 수.
SEEDS = (
    (
        "LOW_INGREDIENT",
        "재료 적게 드는 요리",
        "준비할 재료가 다섯 가지 이하인 요리",
        "ingredient_count",
        {"op": "lte", "value": 5, "exclude_pantry": True},
        10,
        1,
    ),  # 301건
    (
        "FEW_STEPS",
        "손 덜 가는 간단 요리",
        "조리 단계가 다섯 개 이하인 요리",
        "step_count",
        {"op": "lte", "value": 5},
        10,
        2,
    ),  # 794건
    (
        "LIGHT",
        "채소·과일 위주 가볍게",
        "채소와 과일이 재료의 절반 이상인 요리",
        "ingredient_category",
        {"op": "ratio_gte", "value": 0.6, "category_names": ["채소류", "버섯류", "과일류"], "exclude_pantry": True},
        10,
        3,
    ),  # 296건
    (
        "MEAT",
        "고기 든든하게",
        "육류를 쓰는 요리",
        "ingredient_category",
        # `난류`(달걀)를 넣었더니 `레몬 커드` 같은 디저트가 걸렸습니다. 육류만 봅니다.
        {"op": "contains", "category_names": ["육류"], "exclude_pantry": True},
        10,
        4,
    ),  # 458건
    (
        "QUICK_15MIN",
        "15분 안에 끝나는 요리",
        "조리 시간이 15분 이하인 요리",
        "cook_time",
        {"op": "lte", "value": 15},
        10,
        5,
    ),  # 233건
)


def upgrade() -> None:
    table = sa.table(
        "bubble_keyword",
        sa.column("keyword_id", sa.Text),
        sa.column("label", sa.Text),
        sa.column("description", sa.Text),
        sa.column("rule_type", sa.Text),
        # 실제 컬럼이 JSONB 다. Text 로 선언하면 psycopg 가 VARCHAR 로 캐스팅해 거부당한다.
        sa.column("rule_spec", postgresql.JSONB),
        sa.column("min_candidates", sa.Integer),
        sa.column("display_order", sa.Integer),
    )
    op.bulk_insert(
        table,
        [
            {
                "keyword_id": keyword_id,
                "label": label,
                "description": description,
                "rule_type": rule_type,
                "rule_spec": rule_spec,
                "min_candidates": min_candidates,
                "display_order": display_order,
            }
            for keyword_id, label, description, rule_type, rule_spec, min_candidates, display_order in SEEDS
        ],
    )


def downgrade() -> None:
    ids = ", ".join(f"'{seed[0]}'" for seed in SEEDS)
    op.execute(f"DELETE FROM bubble_keyword WHERE keyword_id IN ({ids})")
