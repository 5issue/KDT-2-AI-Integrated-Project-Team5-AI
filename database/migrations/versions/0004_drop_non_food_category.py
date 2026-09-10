"""식용이 아닌 카테고리 제거

농식품 표준품목코드(`MAFRA_STD_ITEM_CODE`)는 농림수산식품 전체를 다루므로
난·화훼·수목·목재·임산물·사료가 포함되어 있다. 신선식품 커머스에서는 쓰지 않는다.

적재 시점에 `metadata.is_edible` 로 표시해 두었으므로 그 값으로 지운다.
자식부터 지워야 `parent_id` 참조가 걸리지 않는다.

지운 행은 downgrade 로 되살아나지 않는다. 스키마 롤백을 막지 않으려고 downgrade 는 비워 두고,
분류가 다시 필요하면 `02_category_mafra_item_code.ipynb` 를 재실행한다.

Revision ID: 0004_drop_non_food_category
Revises: 0003_timestamptz_unify
"""

from alembic import op

revision = "0004_drop_non_food_category"
down_revision = "0003_timestamptz_unify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for depth in (2, 1, 0):
        op.execute(
            "DELETE FROM category "
            "WHERE metadata ->> 'source' = 'MAFRA_STD_ITEM_CODE' "
            "  AND (metadata ->> 'is_edible')::boolean IS FALSE "
            f"  AND depth = {depth}"
        )


def downgrade() -> None:
    """지운 행은 복구하지 않는다.

    데이터만 지우는 마이그레이션이라 되돌릴 원본이 없다. 여기서 예외를 던지면
    아래 리비전으로의 스키마 롤백까지 막히므로 아무것도 하지 않는다.
    분류가 다시 필요하면 `02_category_mafra_item_code.ipynb` 를 재실행한다.
    """
