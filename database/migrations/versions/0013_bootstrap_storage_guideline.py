"""초기 Production에 누락된 storage_guideline 최종 스키마를 생성한다.

KIPIL에는 이미 이 표가 있으므로 그대로 두고, baseline 표만 존재하는 초기 Production에서만
생성한다. PR #10 병합 중 최초 생성 revision이 사라진 이력을 복구하는 migration이다.

Revision ID: 0013_storage_bootstrap
Revises: 0012_storage_service_key
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_storage_bootstrap"
down_revision = "0012_storage_service_key"
branch_labels = None
depends_on = None

# 이 revision 이 표를 만들었다는 영구 표식이다. downgrade 가 지울 권한을 여기서만 얻는다.
# 표식이 없으면 KIPIL 처럼 먼저 있던 표이므로 건드리지 않는다. 만든 적 없는 revision 이
# 삭제 권한까지 갖는 구조를 만들지 않는다.
OWNER_MARK = "created_by:0013_storage_bootstrap"


def upgrade() -> None:
    """표가 없는 대상에 서비스 조회용 최종 스키마를 생성한다."""
    if sa.inspect(op.get_bind()).has_table("storage_guideline"):
        return
    op.create_table(
        "storage_guideline",
        sa.Column("storage_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("ingredient_id", sa.BigInteger(), nullable=False),
        sa.Column("source_food_name", sa.Text(), nullable=False),
        sa.Column("source_food_subtitle", sa.Text(), nullable=True),
        sa.Column("source_slot", sa.Text(), nullable=False),
        sa.Column("storage_location", sa.String(length=20), nullable=False),
        sa.Column("storage_context", sa.String(length=20), server_default="일반", nullable=False),
        sa.Column("duration_min", sa.Numeric(10, 2), nullable=True),
        sa.Column("duration_max", sa.Numeric(10, 2), nullable=True),
        sa.Column("duration_unit", sa.String(length=10), nullable=True),
        sa.Column("duration_text", sa.Text(), nullable=False),
        sa.Column("storage_tips", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("storage_location IN ('냉장', '냉동', '상온')", name="ck_storage_guideline_location"),
        sa.CheckConstraint(
            "storage_context IN ('일반', '구매후', '개봉후', '해동후')",
            name="ck_storage_guideline_context",
        ),
        sa.CheckConstraint(
            "duration_unit IS NULL OR duration_unit IN ('시간', '일', '주', '개월', '년')",
            name="ck_storage_guideline_duration_unit",
        ),
        sa.CheckConstraint(
            "duration_min IS NULL OR duration_max IS NULL OR duration_min <= duration_max",
            name="ck_storage_guideline_duration_range",
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredient.ingredient_id"],
            name="fk_storage_guideline_ingredient",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("storage_id", name="pk_storage_guideline"),
        sa.UniqueConstraint(
            "ingredient_id",
            "storage_location",
            "storage_context",
            name="uq_storage_guideline_query",
        ),
    )
    op.create_index(
        "idx_storage_guideline_ingredient_lookup",
        "storage_guideline",
        ["ingredient_id", "storage_location", "storage_context"],
    )
    op.execute(f"COMMENT ON TABLE storage_guideline IS '{OWNER_MARK}'")


def downgrade() -> None:
    """이 revision이 만든 표일 때만 제거한다.

    upgrade 는 표가 있으면 건너뛰는데 downgrade 가 있으면 지우면, 먼저 있던 표와 그 데이터가
    사라진다. 표식을 확인해 직접 만든 경우에만 지운다.
    """
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("storage_guideline"):
        return
    mark = bind.execute(sa.text("SELECT obj_description('public.storage_guideline'::regclass, 'pg_class')")).scalar()
    if mark != OWNER_MARK:
        return
    op.drop_index("idx_storage_guideline_ingredient_lookup", table_name="storage_guideline")
    op.drop_table("storage_guideline")
