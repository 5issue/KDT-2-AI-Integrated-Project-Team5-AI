"""product.brand_id 를 brand_name 으로 바꾸고 크롤 값을 채운다

`product.brand_id BIGINT` 는 **가리킬 표가 없는 컬럼이었다.** `brand` 테이블이
스키마에 없고, FK 제약도 걸려 있지 않으며, 2,553행 전부 NULL 이다.

한편 크롤 원천(`kurly_products_food` -> `product_raw`)에는 브랜드가 문자열로
2,094행(82%) 들어 있고, 적재기가 그것을 `product.metadata->>'brand'` 에 실어
이미 DB 에 넣어 뒀다. 가리킬 표 없는 정수 키를 들고 있으면서 실제 값은 jsonb
안에 숨어 있는 상태였다.

브랜드 표를 따로 둘 이유가 지금은 없다. 브랜드에 매달 속성(로고, 소개, 공식몰)이
아직 없고, 이름 외에 쓸 일이 없다. 표를 만들면 적재 때마다 브랜드 upsert 가
하나 더 붙고, 이름 표기가 흔들릴 때(`Kurly's` / `컬리`) 행이 갈라진다.
필요해지면 그때 `brand_name` 을 seed 로 표를 만들면 된다. 지금 만들면 빈 표다.

`metadata.brand` 는 지우지 않는다. 원천이 준 값 그대로의 기록이고,
`brand_name` 은 거기서 공백을 다듬어 꺼낸 표시용 값이다.

Revision ID: 0011_product_brand_name
Revises: 0010_bubble_view_perf
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_product_brand_name"
down_revision = "0010_bubble_view_perf"
branch_labels = None
depends_on = None

# 실측 최대 길이는 15 자다(`농협안심한우` 등). 100 이면 한참 남는다.
BRAND_NAME_LENGTH = 100


def upgrade() -> None:
    op.add_column("product", sa.Column("brand_name", sa.String(BRAND_NAME_LENGTH), nullable=True))

    # 이미 적재된 2,094행을 metadata 에서 꺼내 옮긴다. 빈 문자열은 NULL 로 둔다.
    # 빈 문자열을 넣으면 "브랜드 없음" 과 "브랜드가 빈 문자열" 이 구분되지 않는다.
    op.execute(
        f"""
        UPDATE product
        SET brand_name = LEFT(NULLIF(BTRIM(metadata ->> 'brand'), ''), {BRAND_NAME_LENGTH})
        WHERE NULLIF(BTRIM(COALESCE(metadata ->> 'brand', '')), '') IS NOT NULL
        """
    )

    # 가리킬 표가 없는 컬럼이다. 남겨 두면 다음 사람이 brand 테이블이 있다고 읽는다.
    op.drop_column("product", "brand_id")


def downgrade() -> None:
    op.add_column("product", sa.Column("brand_id", sa.BigInteger(), nullable=True))
    op.drop_column("product", "brand_name")
