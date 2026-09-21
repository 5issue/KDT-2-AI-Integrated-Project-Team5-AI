"""Product 대표 이미지 URL.

상품 카드와 상세가 이미지를 쓰는데 `product` 에는 이미지 컬럼이 없다. `recipe` 와
`recipe_step` 에만 있다. 상품 하나에 표시용 이미지 하나를 둔다. 상세·브랜드·후기 이미지는
원천 크롤 결과에만 두고 서비스 표로 올리지 않는다.

Revision ID: 0015_product_image_url
Revises: 0014_alignment_constraints
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_product_image_url"
down_revision = "0014_alignment_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """대표 이미지 URL 컬럼을 더한다."""
    op.add_column("product", sa.Column("image_url", sa.Text(), nullable=True))
    op.execute(
        "COMMENT ON COLUMN product.image_url IS '상품 대표 이미지 URL. 상세, 브랜드, 후기 이미지는 원천에만 보존한다.'"
    )


def downgrade() -> None:
    """컬럼을 지운다."""
    op.drop_column("product", "image_url")
