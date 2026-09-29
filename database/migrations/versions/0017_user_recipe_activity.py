"""찜한 레시피와 최근 본 레시피 표.

My 레시피 화면 하단의 "최근 본 레시피" / "찜한 레시피" 두 줄을 받치는 사용자 행동 표다.
백엔드 ERD 에는 레시피 단위의 찜·조회 이력이 없어 AI 파트가 따로 둔다.

- `user_recipe_favorite`: 사용자가 하트를 누른 레시피. (user_id, recipe_id) 한 쌍이 한 행이고
  `created_at` 순으로 목록을 낸다. 두 번 누르면 행이 아니라 409 다.
- `user_recipe_view`: 사용자가 상세를 본 레시피. 같은 레시피를 다시 보면 행을 늘리지 않고
  `viewed_at` 만 당긴다. 화면이 "최근 본" 목록에 같은 레시피를 두 번 보여 줄 이유가 없다.
  조회 로그 원본이 필요해지면 그때 append-only 표를 따로 둔다.

목록은 항상 사용자 하나의 최근 N 건만 읽으므로 (user_id, 시각 DESC) 인덱스 하나로 충분하다.

Revision ID: 0017_user_recipe_activity
Revises: 0016_storage_id_identity
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_user_recipe_activity"
down_revision = "0016_storage_id_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_recipe_favorite",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("recipe_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "recipe_id", name="pk_user_recipe_favorite"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.user_id"], name="fk_app_user_to_user_recipe_favorite", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipe.recipe_id"], name="fk_recipe_to_user_recipe_favorite", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_user_recipe_favorite_user_created",
        "user_recipe_favorite",
        ["user_id", sa.text("created_at DESC")],
    )
    op.execute("COMMENT ON TABLE user_recipe_favorite IS '사용자가 찜한 레시피. 한 쌍당 한 행.'")

    op.create_table(
        "user_recipe_view",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("recipe_id", sa.BigInteger(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "recipe_id", name="pk_user_recipe_view"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.user_id"], name="fk_app_user_to_user_recipe_view", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipe.recipe_id"], name="fk_recipe_to_user_recipe_view", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_user_recipe_view_user_viewed",
        "user_recipe_view",
        ["user_id", sa.text("viewed_at DESC")],
    )
    op.execute("COMMENT ON TABLE user_recipe_view IS '사용자가 최근 본 레시피. 다시 보면 viewed_at 만 갱신.'")


def downgrade() -> None:
    op.drop_index("ix_user_recipe_view_user_viewed", table_name="user_recipe_view")
    op.drop_table("user_recipe_view")
    op.drop_index("ix_user_recipe_favorite_user_created", table_name="user_recipe_favorite")
    op.drop_table("user_recipe_favorite")
