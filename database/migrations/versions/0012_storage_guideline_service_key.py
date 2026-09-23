"""보관 지침을 서비스 조회 키별 대표 1건으로 정리한다.

FoodKeeper 원천의 ``source_item_id`` 는 staging에서만 variant를 식별하는 데 사용한다.
서비스 ``storage_guideline`` 테이블은 Ingredient와 화면 조회에 필요한 장소·상황 조합을
정본으로 보관하므로, 자연키를 다음 세 값으로 바꾼다.

    (ingredient_id, storage_location, storage_context)

같은 키의 여러 원천 후보는 data_pipeline 적재 단계에서 검토·대표 선택을 거친 뒤 한 행만
들어온다. ``user_fridge`` 구조는 추천 SQL에서 사용하지 않으므로 이 revision에서 바꾸지 않는다.

Revision ID: 0012_storage_service_key
Revises: 0011_product_brand_name
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_storage_service_key"
down_revision = "0011_product_brand_name"
branch_labels = None
depends_on = None


def _drop_source_rule_key() -> None:
    """기존 DB가 constraint 또는 unique index 어느 쪽이든 안전하게 제거한다."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'storage_guideline'::regclass
                  AND conname = 'uq_storage_guideline_source_rule'
            ) THEN
                ALTER TABLE storage_guideline
                    DROP CONSTRAINT uq_storage_guideline_source_rule;
            ELSIF EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'storage_guideline'
                  AND indexname = 'uq_storage_guideline_source_rule'
            ) THEN
                DROP INDEX uq_storage_guideline_source_rule;
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    """원천 variant 키를 서비스 조회 키로 교체한다."""
    if not sa.inspect(op.get_bind()).has_table("storage_guideline"):
        return
    _drop_source_rule_key()

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT ingredient_id, storage_location, storage_context
                FROM storage_guideline
                GROUP BY ingredient_id, storage_location, storage_context
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate storage guideline service keys must be resolved before migration';
            END IF;
        END $$;
        """
    )

    # source_item_id는 final table에 남기지 않는다. staging에만 유지해 대표 후보를 고른다.
    op.drop_column("storage_guideline", "source_item_id")
    op.create_unique_constraint(
        "uq_storage_guideline_query",
        "storage_guideline",
        ["ingredient_id", "storage_location", "storage_context"],
    )


def downgrade() -> None:
    """원천 식별자를 복원할 수 없으므로 schema만 되돌린다."""
    if not sa.inspect(op.get_bind()).has_table("storage_guideline"):
        return
    op.drop_constraint("uq_storage_guideline_query", "storage_guideline", type_="unique")
    op.add_column("storage_guideline", sa.Column("source_item_id", sa.Text(), nullable=True))
    op.execute("UPDATE storage_guideline SET source_item_id = 'legacy_' || storage_id")
    op.alter_column("storage_guideline", "source_item_id", nullable=False)
    op.create_unique_constraint(
        "uq_storage_guideline_source_rule",
        "storage_guideline",
        ["ingredient_id", "source_item_id", "source_slot", "storage_location", "storage_context"],
    )
