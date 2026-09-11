"""보관 관련 열거값을 한국어로 통일

`product.storage_type`, `storage_guideline.storage_location`,
`storage_guideline.storage_context`, `storage_guideline.duration_unit` 네 컬럼이
영어 상수로 적재되어 있었다. 이 값들은 전부 화면에 그대로 나가는 값이다.
영어로 두면 서빙에서 한 번, 프런트에서 한 번 되돌려야 하고 그 대응표가 두 벌이 되면
언젠가 갈라진다. 저장 시점에 한 벌로 모은다.

대응은 `docs/product-ingredient-storage-normalization-guide.md` 4.3 / 5.4 / 5.5 절을 따른다.

`duration_unit` 은 2단계 LLM 이 원문에서 읽어 온 값이라 `Years` 100건과 `Year` 3건처럼
단수·복수가 섞여 있었다. 같은 뜻이면 한 값이어야 하므로 함께 모은다.

source_slot 은 바꾸지 않는다. FoodKeeper 원문 식별자이므로 원천 표기를 유지한다.
slot 과 location/context 의 대응을 CHECK 로 못박지도 않는다. 못박으면 원천이 slot 을
하나 늘릴 때마다 마이그레이션이 필요하고, FoodKeeper 가 아닌 원천을 붙일 때도 걸린다.
변환은 적재기(`data_pipeline.domain.SLOT_DERIVATION`)가 책임지고,
DB 는 "허용된 값 중 하나" 까지만 본다.

Revision ID: 0007_korean_storage_enums
Revises: 0006_bubble_keyword_seed
"""

from alembic import op

revision = "0007_korean_storage_enums"
down_revision = "0006_bubble_keyword_seed"
branch_labels = None
depends_on = None

LOCATIONS = {"REFRIGERATOR": "냉장", "FREEZER": "냉동", "PANTRY": "상온"}
CONTEXTS = {
    "NOT_APPLICABLE": "일반",
    "FROM_PURCHASE": "구매후",
    "AFTER_OPENING": "개봉후",
    "AFTER_THAWING": "해동후",
}
STORAGE_TYPES = {"COLD": "냉장", "FROZEN": "냉동", "AMBIENT_TEMPERATURE": "상온"}
# 단수형도 함께 받는다. 실제로 `Year` 가 3건 들어와 있었다.
DURATION_UNITS = {
    "Hour": "시간",
    "Hours": "시간",
    "Day": "일",
    "Days": "일",
    "Week": "주",
    "Weeks": "주",
    "Month": "개월",
    "Months": "개월",
    "Year": "년",
    "Years": "년",
}


def _case(column: str, mapping: dict[str, str]) -> str:
    """CASE 문 한 덩어리. 대응표에 없는 값은 원문 그대로 둔다."""
    arms = " ".join(f"WHEN '{before}' THEN '{after}'" for before, after in mapping.items())
    return f"UPDATE {{table}} SET {column} = CASE {column} {arms} ELSE {column} END"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """CHECK 본문."""
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    # CHECK 가 옛 값을 붙들고 있어 UPDATE 보다 먼저 떼어낸다.
    op.drop_constraint("ck_storage_guideline_location", "storage_guideline", type_="check")
    op.drop_constraint("ck_storage_guideline_context", "storage_guideline", type_="check")

    op.execute(_case("storage_location", LOCATIONS).format(table="storage_guideline"))
    op.execute(_case("storage_context", CONTEXTS).format(table="storage_guideline"))
    op.execute(_case("duration_unit", DURATION_UNITS).format(table="storage_guideline"))
    op.execute(_case("storage_type", STORAGE_TYPES).format(table="product"))

    op.create_check_constraint(
        "ck_storage_guideline_location",
        "storage_guideline",
        _in_list("storage_location", ("냉장", "냉동", "상온")),
    )
    op.create_check_constraint(
        "ck_storage_guideline_context",
        "storage_guideline",
        _in_list("storage_context", ("일반", "구매후", "개봉후", "해동후")),
    )
    op.create_check_constraint(
        "ck_storage_guideline_duration_unit",
        "storage_guideline",
        "duration_unit IS NULL OR " + _in_list("duration_unit", ("시간", "일", "주", "개월", "년")),
    )
    # product.storage_type 은 아직 비어 있는 행이 1,551 개라 NULL 을 허용한다.
    op.create_check_constraint(
        "ck_product_storage_type",
        "product",
        "storage_type IS NULL OR " + _in_list("storage_type", ("냉장", "냉동", "상온")),
    )


def downgrade() -> None:
    op.drop_constraint("ck_product_storage_type", "product", type_="check")
    op.drop_constraint("ck_storage_guideline_duration_unit", "storage_guideline", type_="check")
    op.drop_constraint("ck_storage_guideline_context", "storage_guideline", type_="check")
    op.drop_constraint("ck_storage_guideline_location", "storage_guideline", type_="check")

    # 되돌릴 때는 단수형을 살릴 수 없다. `Year` 3건은 `Years` 로 합쳐진 채 남는다.
    op.execute(_case("storage_type", {v: k for k, v in STORAGE_TYPES.items()}).format(table="product"))
    op.execute(
        _case("duration_unit", {"시간": "Hours", "일": "Days", "주": "Weeks", "개월": "Months", "년": "Years"}).format(
            table="storage_guideline"
        )
    )
    op.execute(_case("storage_context", {v: k for k, v in CONTEXTS.items()}).format(table="storage_guideline"))
    op.execute(_case("storage_location", {v: k for k, v in LOCATIONS.items()}).format(table="storage_guideline"))

    op.create_check_constraint(
        "ck_storage_guideline_location",
        "storage_guideline",
        _in_list("storage_location", ("REFRIGERATOR", "FREEZER", "PANTRY")),
    )
    op.create_check_constraint(
        "ck_storage_guideline_context",
        "storage_guideline",
        _in_list("storage_context", ("FROM_PURCHASE", "AFTER_OPENING", "AFTER_THAWING", "NOT_APPLICABLE")),
    )
