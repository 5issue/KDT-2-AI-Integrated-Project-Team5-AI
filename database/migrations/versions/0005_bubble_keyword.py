"""bubble_keyword - 홈 화면 버블 UI 키워드

화면 상단의 타원형 말풍선(버블)에 무엇을 띄울지 관리하는 표다.
라벨과 필터 규칙을 코드가 아니라 데이터로 두어, 문구를 바꾸거나 후보를 갈아끼울 때
배포 없이 처리한다.

`rule_spec` 을 jsonb 로 둔 이유는 규칙마다 필요한 인자가 다르기 때문이다.
재료 수는 임계값 하나면 되지만, 분류 비율은 대상 코드 목록과 비율이 함께 필요하다.

`min_candidates` 는 이 버블을 눌렀을 때 최소 몇 개는 나와야 하는지다.
후보가 그보다 적으면 화면에 내지 않는다. 실제로 '전자레인지·에어프라이어' 가
28건뿐이라 데이터가 줄면 걸릴 수 있다.

Revision ID: 0005_bubble_keyword
Revises: 0004_drop_non_food_category
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_bubble_keyword"
down_revision = "0004_drop_non_food_category"
branch_labels = None
depends_on = None

RULE_TYPES = (
    "ingredient_count",
    "step_count",
    "ingredient_category",
    "cook_time",
    "servings",
    "text_search",
    "seasonal",
    "popularity",
)


def upgrade() -> None:
    op.create_table(
        "bubble_keyword",
        sa.Column("keyword_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("rule_spec", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("min_candidates", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("rule_version", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("keyword_id"),
        sa.CheckConstraint("min_candidates > 0", name="bubble_keyword_min_candidates_positive"),
        sa.CheckConstraint(
            "rule_type IN (" + ", ".join(f"'{name}'" for name in RULE_TYPES) + ")",
            name="bubble_keyword_rule_type",
        ),
    )
    op.create_index(
        "bubble_keyword_active_order_idx",
        "bubble_keyword",
        ["display_order"],
        postgresql_where=sa.text("is_active"),
    )

    op.execute(
        "COMMENT ON TABLE bubble_keyword IS "
        "'홈 화면 버블 UI 키워드. 라벨과 필터 규칙을 데이터로 두어 배포 없이 바꾼다.'"
    )
    op.execute("COMMENT ON COLUMN bubble_keyword.rule_spec IS '규칙 인자. rule_type 마다 모양이 다르다.'")
    op.execute(
        "COMMENT ON COLUMN bubble_keyword.min_candidates IS "
        "'이보다 후보가 적으면 화면에 내지 않는다. 빈 버블을 누르게 하지 않으려는 것.'"
    )


def downgrade() -> None:
    op.drop_index("bubble_keyword_active_order_idx", table_name="bubble_keyword")
    op.drop_table("bubble_keyword")
