"""Product, Ingredient, Storage Guideline, User Fridge alignment.

FoodKeeper guidance is reusable Ingredient knowledge. It is not copied to every
Product SKU. This revision completes the following service path:

    user_fridge -> product -> product_ingredient -> ingredient -> storage_guideline

The existing production baseline already has the Ingredient hierarchy, Ingredient
source key, Product source key, and array aliases. Therefore this migration adds
their missing integrity constraints, creates storage_guideline, and changes
user_fridge to Product-based storage.

Revision ID: 0005_product_storage_alignment
Revises: 0004_drop_non_food_category
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_product_storage_alignment"
down_revision = "0004_drop_non_food_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Align the existing production baseline with Product-Ingredient storage."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT source_identity_key
                FROM ingredient
                GROUP BY source_identity_key
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate ingredient.source_identity_key values must be resolved before migration';
            END IF;
        END $$;
        """
    )
    op.create_unique_constraint(
        "uq_ingredient_source_identity",
        "ingredient",
        ["source_identity_key"],
    )
    op.create_foreign_key(
        "fk_ingredient_parent",
        "ingredient",
        "ingredient",
        ["parent_ingredient_id"],
        ["ingredient_id"],
        ondelete="RESTRICT",
    )
    op.create_index("idx_ingredient_parent", "ingredient", ["parent_ingredient_id"])

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT source_type, source_product_id
                FROM product
                GROUP BY source_type, source_product_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate product source keys must be resolved before migration';
            END IF;
        END $$;
        """
    )
    op.create_unique_constraint(
        "uq_product_source",
        "product",
        ["source_type", "source_product_id"],
    )
    op.execute(
        """
        UPDATE product
        SET storage_type = CASE storage_type
            WHEN 'COLD' THEN '냉장'
            WHEN 'FROZEN' THEN '냉동'
            WHEN 'AMBIENT_TEMPERATURE' THEN '상온'
            ELSE storage_type
        END;
        """
    )
    op.create_check_constraint(
        "ck_product_storage_type",
        "product",
        "storage_type IS NULL OR storage_type IN ('냉장', '냉동', '상온')",
    )
    op.create_check_constraint(
        "ck_product_stock_nonnegative",
        "product",
        "stock_quantity IS NULL OR stock_quantity >= 0",
    )

    op.create_table(
        "storage_guideline",
        sa.Column(
            "storage_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("ingredient_id", sa.BigInteger(), nullable=False),
        # FoodKeeper source IDs are strings such as ``fk_134``.
        sa.Column("source_item_id", sa.Text(), nullable=False),
        sa.Column("source_food_name", sa.Text(), nullable=False),
        sa.Column("source_food_subtitle", sa.Text(), nullable=True),
        sa.Column("source_slot", sa.Text(), nullable=False),
        sa.Column("storage_location", sa.String(length=20), nullable=False),
        sa.Column(
            "storage_context",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'일반'"),
        ),
        sa.Column("duration_min", sa.Numeric(10, 2), nullable=True),
        sa.Column("duration_max", sa.Numeric(10, 2), nullable=True),
        sa.Column("duration_unit", sa.String(length=10), nullable=True),
        sa.Column("duration_text", sa.Text(), nullable=False),
        sa.Column("storage_tips", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredient.ingredient_id"],
            name="fk_storage_guideline_ingredient",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("storage_id", name="pk_storage_guideline"),
        sa.UniqueConstraint(
            "source_item_id",
            "source_slot",
            name="uq_storage_guideline_source_slot",
        ),
        sa.CheckConstraint(
            "btrim(source_food_name) <> ''",
            name="ck_storage_guideline_source_food_name_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(duration_text) <> ''",
            name="ck_storage_guideline_duration_text_not_blank",
        ),
        sa.CheckConstraint(
            "source_slot IN ("
            "'pantry', 'dop_pantry', 'pantry_after_opening', "
            "'refrigerate', 'dop_refrigerate', "
            "'refrigerate_after_opening', 'refrigerate_after_thawing', "
            "'freeze', 'dop_freeze'"
            ")",
            name="ck_storage_guideline_source_slot",
        ),
        sa.CheckConstraint(
            "storage_location IN ('냉장', '냉동', '상온')",
            name="ck_storage_guideline_location",
        ),
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
        sa.CheckConstraint(
            "(duration_min IS NULL AND duration_max IS NULL AND duration_unit IS NULL) "
            "OR (duration_unit IS NOT NULL AND (duration_min IS NOT NULL OR duration_max IS NOT NULL))",
            name="ck_storage_guideline_duration_complete",
        ),
        sa.CheckConstraint(
            "(source_slot IN ('pantry', 'refrigerate', 'freeze') "
            "AND storage_context = '일반') "
            "OR (source_slot IN ('dop_pantry', 'dop_refrigerate', 'dop_freeze') "
            "AND storage_context = '구매후') "
            "OR (source_slot IN ('pantry_after_opening', 'refrigerate_after_opening') "
            "AND storage_context = '개봉후') "
            "OR (source_slot = 'refrigerate_after_thawing' "
            "AND storage_context = '해동후')",
            name="ck_storage_guideline_slot_context",
        ),
        sa.CheckConstraint(
            "(source_slot IN ('pantry', 'dop_pantry', 'pantry_after_opening') "
            "AND storage_location = '상온') "
            "OR (source_slot IN ("
            "'refrigerate', 'dop_refrigerate', "
            "'refrigerate_after_opening', 'refrigerate_after_thawing'"
            ") AND storage_location = '냉장') "
            "OR (source_slot IN ('freeze', 'dop_freeze') "
            "AND storage_location = '냉동')",
            name="ck_storage_guideline_slot_location",
        ),
    )
    op.create_index(
        "idx_storage_guideline_ingredient_lookup",
        "storage_guideline",
        ["ingredient_id", "storage_location", "storage_context"],
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT user_id, product_id
                FROM user_fridge
                GROUP BY user_id, product_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate user/product fridge rows must be merged before migration';
            END IF;
        END $$;
        """
    )
    op.drop_constraint("pk_user_fridge", "user_fridge", type_="primary")
    op.drop_constraint("fk_ingredient_to_user_fridge", "user_fridge", type_="foreignkey")
    op.drop_column("user_fridge", "ingredient_id")
    op.add_column(
        "user_fridge",
        sa.Column("storage_location", sa.String(length=20), nullable=True),
    )
    op.create_primary_key("pk_user_fridge", "user_fridge", ["user_id", "product_id"])
    op.create_check_constraint(
        "ck_user_fridge_storage_location",
        "user_fridge",
        "storage_location IS NULL OR storage_location IN ('냉장', '냉동', '상온')",
    )


def downgrade() -> None:
    """Reverse structural changes. Product source data remains unchanged."""

    op.drop_constraint("ck_user_fridge_storage_location", "user_fridge", type_="check")
    op.drop_constraint("pk_user_fridge", "user_fridge", type_="primary")
    op.drop_column("user_fridge", "storage_location")
    op.add_column("user_fridge", sa.Column("ingredient_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE user_fridge AS fridge
        SET ingredient_id = mapped.ingredient_id
        FROM (
            SELECT product_id, min(ingredient_id) AS ingredient_id
            FROM product_ingredient
            WHERE role = 'PRIMARY'
            GROUP BY product_id
            HAVING count(*) = 1
        ) AS mapped
        WHERE fridge.product_id = mapped.product_id;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM user_fridge WHERE ingredient_id IS NULL) THEN
                RAISE EXCEPTION
                    'cannot restore user_fridge.ingredient_id without one PRIMARY Ingredient per Product';
            END IF;
        END $$;
        """
    )
    op.alter_column("user_fridge", "ingredient_id", nullable=False)
    op.create_foreign_key(
        "fk_ingredient_to_user_fridge",
        "user_fridge",
        "ingredient",
        ["ingredient_id"],
        ["ingredient_id"],
    )
    op.create_primary_key(
        "pk_user_fridge",
        "user_fridge",
        ["ingredient_id", "user_id", "product_id"],
    )

    op.drop_index("idx_storage_guideline_ingredient_lookup", table_name="storage_guideline")
    op.drop_table("storage_guideline")

    op.drop_constraint("ck_product_stock_nonnegative", "product", type_="check")
    op.drop_constraint("ck_product_storage_type", "product", type_="check")
    op.drop_constraint("uq_product_source", "product", type_="unique")

    op.drop_index("idx_ingredient_parent", table_name="ingredient")
    op.drop_constraint("fk_ingredient_parent", "ingredient", type_="foreignkey")
    op.drop_constraint("uq_ingredient_source_identity", "ingredient", type_="unique")
