"""표가 먼저 있던 DB 의 storage_guideline.storage_id 에 identity 를 붙인다.

0013 은 표가 없는 DB 에만 최종 스키마(`storage_id` GENERATED ALWAYS AS IDENTITY)를 만들고, KIPIL 처럼
표가 먼저 있던 DB 는 건너뛴다. 그래서 KIPIL 의 `storage_id` 는 기본값도 identity 도 없는 bigint 로 남아,
`storage_id` 를 주지 않는 적재 SQL(`data_pipeline/sql/004`)과 `load_storage_guideline` 이 NOT NULL 로
실패한다. `test_load_transaction.py` 가 이것을 잡았다.

identity 가 이미 있으면(0013 이 만든 표) 아무것도 하지 않는다. 붙일 때는 기존 최댓값 다음부터 번호를
이어 기존 행과 겹치지 않게 한다.

Revision ID: 0016_storage_id_identity
Revises: 0015_product_image_url
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_storage_id_identity"
down_revision = "0015_product_image_url"
branch_labels = None
depends_on = None

# 이 revision 이 identity 를 붙였다는 표식이다. downgrade 는 표식이 있을 때만 되돌린다.
# 0013 이 처음부터 identity 로 만든 표의 identity 를 지우지 않기 위해서다.
OWNER_MARK = "identity_added_by:0016_storage_id_identity"


def _storage_id_is_identity() -> bool:
    return (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT is_identity = 'YES' FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'storage_guideline' AND column_name = 'storage_id'"
            )
        )
        .scalar()
        is True
    )


def upgrade() -> None:
    """identity 가 없을 때만 0013 과 같은 GENERATED ALWAYS identity 를 붙인다."""
    if not sa.inspect(op.get_bind()).has_table("storage_guideline") or _storage_id_is_identity():
        return
    op.execute("ALTER TABLE storage_guideline ALTER COLUMN storage_id ADD GENERATED ALWAYS AS IDENTITY")
    op.execute(
        "SELECT setval(pg_get_serial_sequence('storage_guideline', 'storage_id'), "
        "COALESCE((SELECT MAX(storage_id) FROM storage_guideline), 0) + 1, false)"
    )
    op.execute(f"COMMENT ON COLUMN storage_guideline.storage_id IS '{OWNER_MARK}'")


def downgrade() -> None:
    """이 revision 이 붙인 identity 만 되돌린다."""
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("storage_guideline"):
        return
    mark = bind.execute(
        sa.text(
            "SELECT col_description('storage_guideline'::regclass, attnum) FROM pg_attribute "
            "WHERE attrelid = 'storage_guideline'::regclass AND attname = 'storage_id'"
        )
    ).scalar()
    if mark != OWNER_MARK:
        return
    op.execute("ALTER TABLE storage_guideline ALTER COLUMN storage_id DROP IDENTITY")
    op.execute("COMMENT ON COLUMN storage_guideline.storage_id IS NULL")
