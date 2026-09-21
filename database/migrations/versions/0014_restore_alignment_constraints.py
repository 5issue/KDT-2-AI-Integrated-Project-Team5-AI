"""PR #10 의 정합화 migration 이 머지에서 사라지며 빠진 제약을 되살린다.

`0005_product_ingredient_storage_alignment` 은 번호 재배치 과정에서 파일이 옮겨지지 않고
`0012_storage_guideline_service_key` 로 대체됐다. 0012 에는 storage_guideline 의 서비스
자연키 투영만 남았고, ingredient 와 product 의 제약은 함께 사라졌다.

컬럼은 손으로 먼저 들어가 있어서 조회는 되지만 제약이 없다. 그래서 지금은

- `parent_ingredient_id` 가 없는 재료를 가리켜도 막히지 않는다. 계층 확장 쿼리가
  `WITH RECURSIVE` 로 부모를 따라가므로 순환이 생기면 쿼리가 끝나지 않는다.
- `(source_type, source_product_id)` 와 `source_identity_key` 에 UNIQUE 가 없다.
  멱등 적재가 기대는 자연키가 DB 에 없는 상태다.

`storage_type` CHECK 는 0007 이 이미 만들었으므로 여기서 다시 만들지 않는다.
`sku` 의 NULL 허용은 이미 반영되어 있다.

Revision ID: 0014_alignment_constraints
Revises: 0013_storage_bootstrap
"""

from alembic import op

revision = "0014_alignment_constraints"
down_revision = "0013_storage_bootstrap"
branch_labels = None
depends_on = None


def _guard(message: str, condition: str) -> None:
    """조건에 걸리는 행이 있으면 무엇이 걸렸는지 말하고 멈춘다.

    제약을 그냥 걸면 PostgreSQL 이 위반 행 하나만 알려 준다. 몇 건인지 먼저 세어
    데이터를 고칠지 migration 을 미룰지 판단할 수 있게 한다.
    """
    op.execute(
        f"""
        DO $$
        DECLARE offending bigint;
        BEGIN
            SELECT count(*) INTO offending FROM ({condition}) AS violations;
            IF offending > 0 THEN
                RAISE EXCEPTION '{message} (% 건). 데이터를 먼저 정리해야 한다.', offending;
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    """정합화 제약을 다시 건다. 위반이 있으면 걸기 전에 멈춘다."""
    # NULL 은 UNIQUE 가 허용하므로 중복 판정에서 뺀다. 넣으면 NULL 이 둘만 있어도 걸린다.
    _guard(
        "ingredient.source_identity_key 가 중복이다",
        """
        SELECT 1 FROM ingredient
        WHERE source_identity_key IS NOT NULL
        GROUP BY source_identity_key HAVING count(*) > 1
        """,
    )
    _guard(
        "ingredient.parent_ingredient_id 가 없는 재료를 가리킨다",
        """
        SELECT 1 FROM ingredient child
        WHERE child.parent_ingredient_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM ingredient parent WHERE parent.ingredient_id = child.parent_ingredient_id
          )
        """,
    )
    _guard(
        "ingredient 가 자기 자신을 부모로 가리킨다",
        "SELECT 1 FROM ingredient WHERE parent_ingredient_id = ingredient_id",
    )
    _guard(
        "product 의 (source_type, source_product_id) 가 중복이다",
        """
        SELECT 1 FROM product
        WHERE source_type IS NOT NULL AND source_product_id IS NOT NULL
        GROUP BY source_type, source_product_id HAVING count(*) > 1
        """,
    )
    _guard("product.stock_quantity 가 음수다", "SELECT 1 FROM product WHERE stock_quantity < 0")

    op.create_unique_constraint("uq_ingredient_source_identity", "ingredient", ["source_identity_key"])
    op.create_foreign_key(
        "fk_ingredient_parent",
        "ingredient",
        "ingredient",
        ["parent_ingredient_id"],
        ["ingredient_id"],
        ondelete="RESTRICT",
    )
    # 자식에서 부모로 올라가는 계층 확장이 이 인덱스를 쓴다.
    op.create_index("idx_ingredient_parent", "ingredient", ["parent_ingredient_id"])

    op.create_unique_constraint("uq_product_source", "product", ["source_type", "source_product_id"])
    # 미확인 재고(NULL)와 품절(0)은 다른 값이다. NULL 을 0 으로 읽지 않는다.
    op.create_check_constraint(
        "ck_product_stock_nonnegative",
        "product",
        "stock_quantity IS NULL OR stock_quantity >= 0",
    )


def downgrade() -> None:
    """제약만 되돌린다. 컬럼은 이 revision 이 만든 것이 아니므로 그대로 둔다."""
    op.drop_constraint("ck_product_stock_nonnegative", "product", type_="check")
    op.drop_constraint("uq_product_source", "product", type_="unique")
    op.drop_index("idx_ingredient_parent", table_name="ingredient")
    op.drop_constraint("fk_ingredient_parent", "ingredient", type_="foreignkey")
    op.drop_constraint("uq_ingredient_source_identity", "ingredient", type_="unique")
