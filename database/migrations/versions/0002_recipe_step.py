"""recipe_step 분리와 레시피 대표 사진

조리 순서를 `recipe.description` 에 몰아넣던 것을 단계 단위로 분리한다.
화면이 단계마다 사진과 설명을 함께 보여주므로 단계가 곧 표시 단위다.

원천에 단계 정보가 없는 레시피가 있을 수 있다. 그런 레시피는 `recipe_step` 행을
하나도 갖지 않으며, 조회는 항상 LEFT JOIN 을 전제한다.

Revision ID: 0002_recipe_step
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_recipe_step"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_step",
        sa.Column("recipe_id", sa.BigInteger(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipe.recipe_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recipe_id", "step_no"),
        sa.CheckConstraint("step_no > 0", name="recipe_step_step_no_positive"),
        sa.CheckConstraint(
            "instruction IS NOT NULL OR image_url IS NOT NULL",
            name="recipe_step_has_content",
        ),
    )
    op.execute(
        "COMMENT ON TABLE recipe_step IS "
        "'레시피의 조리 단계. 단계가 없는 레시피는 행을 갖지 않으므로 항상 LEFT JOIN 한다.'"
    )
    op.execute(
        "COMMENT ON COLUMN recipe_step.step_no IS '1부터 시작하는 표시 순서. 원천의 단계 번호가 아니라 정리된 순서다.'"
    )
    op.execute(
        "COMMENT ON COLUMN recipe_step.instruction IS "
        "'단계 설명. 사진만 있고 설명이 없는 단계를 허용하려고 NULL 을 받는다.'"
    )
    op.execute(
        "COMMENT ON COLUMN recipe_step.image_url IS '단계 사진 URL. 원천 이미지를 참조하며 파일을 저장하지 않는다.'"
    )

    op.add_column("recipe", sa.Column("image_url", sa.Text(), nullable=True))
    op.execute("COMMENT ON COLUMN recipe.image_url IS '레시피 대표 사진 URL.'")


def downgrade() -> None:
    op.drop_column("recipe", "image_url")
    op.drop_table("recipe_step")
