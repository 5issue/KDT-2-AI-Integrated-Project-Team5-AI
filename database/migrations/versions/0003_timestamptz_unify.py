"""시각 컬럼을 TIMESTAMPTZ 로 통일

`ingredient_source_map` `storage_guideline` 은 이미 TIMESTAMPTZ 인데 나머지는 TIMESTAMP 라서
같은 시각을 다르게 해석할 수 있다.

기존 값은 모두 서버 `now()` 로 기록됐고 Neon 서버 타임존이 GMT 라서 UTC 로 해석해 변환한다.
서버 타임존이 다른 환경에서 적재한 값이 있다면 적용 전에 확인해야 한다.

Revision ID: 0003_timestamptz_unify
Revises: 0002_recipe_step
"""

from alembic import op

revision = "0003_timestamptz_unify"
down_revision = "0002_recipe_step"
branch_labels = None
depends_on = None

COLUMNS = [
    ("app_user", "created_at"),
    ("category", "created_at"),
    ("ingredient", "created_at"),
    ("order_header", "ordered_at"),
    ("product", "created_at"),
    ("product", "updated_at"),
    ("recipe", "created_at"),
    ("user_fridge", "expires_at"),
    ("user_product_affinity", "last_purchased_at"),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMPTZ USING {column} AT TIME ZONE 'UTC'")


def downgrade() -> None:
    for table, column in COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMP USING {column} AT TIME ZONE 'UTC'")
