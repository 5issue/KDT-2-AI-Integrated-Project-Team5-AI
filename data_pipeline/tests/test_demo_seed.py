"""데모 시드 검증. DB 를 쓰지 않습니다.

여기서 잘못되면 추천 문구 실험의 입력이 통째로 어긋납니다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_pipeline.load.demo_seed import (
    DEMO_USER_BASE,
    DEMO_USER_MAX,
    STOCK_LOW_RANGE,
    STOCK_NORMAL_RANGE,
    DemoReport,
    build_rows,
    build_stock_rows,
    run_demo_seed,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CANDIDATES = [(1000 + index, 2000 + index) for index in range(40)]


def seed(users: int = 10):
    """행 묶음 하나."""
    return build_rows(CANDIDATES, users=users, now=NOW)


def test_same_input_gives_same_data() -> None:
    """난수 시드를 고정했습니다. 실험을 다시 돌릴 때 입력이 달라지면 비교가 안 됩니다."""
    assert seed() == seed()


def test_user_ids_stay_in_the_demo_band() -> None:
    """실제 사용자와 섞이면 지울 때 구분할 수 없습니다."""
    app_users, fridge, affinity, _, _ = seed()

    assert all(user_id >= DEMO_USER_BASE for user_id, _ in app_users)
    assert all(row[1] >= DEMO_USER_BASE for row in fridge)
    assert all(row[0] >= DEMO_USER_BASE for row in affinity)


def test_fridge_only_holds_products_that_have_an_ingredient() -> None:
    """재료가 안 붙은 상품을 넣으면 레시피 매칭이 안 돼 화면이 계속 빕니다."""
    _, fridge, _, _, _ = seed()
    allowed = {(ingredient_id, product_id) for product_id, ingredient_id in CANDIDATES}

    assert all((row[0], row[2]) in allowed for row in fridge)


def test_edge_cases_are_always_present() -> None:
    """난수에 맡기면 사용자 수가 적을 때 안 나옵니다. 앞 두 명에게 고정 배정합니다."""
    app_users, fridge, _, _, edge_cases = seed(users=5)

    assert edge_cases == {"빈 냉장고": 1, "만료만 보유": 1}

    first, second = app_users[0][0], app_users[1][0]
    assert not [row for row in fridge if row[1] == first], "첫 사용자는 냉장고가 비어야 합니다"

    expiries = [row[5] for row in fridge if row[1] == second]
    assert expiries and all(expires < NOW for expires in expiries), "둘째는 전부 만료여야 합니다"


def test_fridge_mixes_expired_and_fresh() -> None:
    """만료 재료를 보유로 치지 않는지 보려면 둘 다 있어야 합니다."""
    _, fridge, _, _, _ = seed(users=12)
    expiries = [row[5] for row in fridge]

    assert any(expires < NOW for expires in expiries)
    assert any(expires > NOW for expires in expiries)


def test_purchase_dates_are_spread_out() -> None:
    """전부 최근이면 `reorder_candidates` 가 항상 빈손이라 검증이 안 됩니다."""
    _, _, affinity, _, _ = seed(users=12)
    ages = [(NOW - row[3]).days for row in affinity]

    assert min(ages) < 30 < max(ages)


def test_popularity_is_long_tailed() -> None:
    """상위 소수가 대부분을 차지해야 인기순 정렬이 의미를 갖습니다."""
    _, _, _, popularity, _ = seed()
    scores = sorted((row[7] for row in popularity), reverse=True)

    assert scores[0] > scores[-1] * 10
    assert len({row[2] for row in popularity}) == len(popularity), "상품이 중복되면 PK 가 깨집니다"


def test_report_says_whether_it_applied() -> None:
    """dry-run 을 실제 적재로 착각하면 빈 DB 로 실험을 시작하게 됩니다."""
    report = DemoReport(users=3)

    assert "안 함(dry-run)" in report.render()
    report.applied = True
    assert "완료" in report.render()


# --- 재고 (`product.stock_quantity`) -----------------------------------------
#
# 이 파이프라인에서 유일하게 지어내는 값이라, 무엇을 보장하는지 여기 못박아 둡니다.

PRODUCT_IDS = list(range(1, 501))


def test_stock_is_reproducible() -> None:
    """시연 중에 품절 상품이 바뀌면 설명할 수가 없습니다."""
    assert build_stock_rows(PRODUCT_IDS) == build_stock_rows(PRODUCT_IDS)


def test_stock_does_not_depend_on_input_order() -> None:
    """상품 조회 순서는 DB 가 정합니다. 그것 때문에 재고가 달라지면 안 됩니다."""
    shuffled = list(reversed(PRODUCT_IDS))
    assert build_stock_rows(shuffled) == build_stock_rows(PRODUCT_IDS)


def test_stock_covers_every_product() -> None:
    """남는 NULL 이 있으면 상품 상세 화면만 값이 비어 갈립니다."""
    rows, _ = build_stock_rows(PRODUCT_IDS)
    assert [product_id for product_id, _ in rows] == PRODUCT_IDS


def test_some_products_are_sold_out() -> None:
    """품절이 하나도 없으면 `stock_quantity > 0` 분기가 한 번도 실행되지 않습니다."""
    rows, sold_out = build_stock_rows(PRODUCT_IDS)

    zeros = [product_id for product_id, quantity in rows if quantity == 0]
    assert len(zeros) == sold_out
    assert 0 < sold_out < len(PRODUCT_IDS) // 10, "품절이 너무 많으면 데모 화면이 비어 보입니다"


def test_stock_quantities_stay_in_range() -> None:
    """음수가 들어가면 `> 0` 조건은 통과시키지 않지만 화면에 그대로 나갑니다."""
    rows, _ = build_stock_rows(PRODUCT_IDS)
    assert all(0 <= quantity <= STOCK_NORMAL_RANGE[1] for _, quantity in rows)


def test_low_stock_products_exist() -> None:
    """'품절 임박' 을 보여 줄 구간이 있어야 합니다."""
    rows, _ = build_stock_rows(PRODUCT_IDS)
    low = [q for _, q in rows if STOCK_LOW_RANGE[0] <= q <= STOCK_LOW_RANGE[1]]
    assert low, "재고가 한 자리인 상품이 하나도 없습니다"


def test_stock_seed_is_independent_of_user_count() -> None:
    """`--users` 만 바꿨는데 품절 상품이 달라지면 데모가 흔들립니다."""
    before, _ = build_stock_rows(PRODUCT_IDS)
    build_rows(CANDIDATES, users=50, now=NOW)
    after, _ = build_stock_rows(PRODUCT_IDS)
    assert before == after


# --- 데모 대역 경계 (코드래빗 리뷰 반영) --------------------------------------


def test_demo_band_is_closed_at_both_ends() -> None:
    """`>= BASE` 만 쓰면 대역 위쪽이 열려 있어, 실제 사용자 id 가 그 위로 올라오면
    남의 냉장고·구매이력을 지웁니다."""
    assert DEMO_USER_MAX > DEMO_USER_BASE
    app_users, fridge, affinity, _, _ = seed(users=50)

    assert all(DEMO_USER_BASE <= user_id <= DEMO_USER_MAX for user_id, _ in app_users)
    assert all(DEMO_USER_BASE <= row[1] <= DEMO_USER_MAX for row in fridge)
    assert all(DEMO_USER_BASE <= row[0] <= DEMO_USER_MAX for row in affinity)


def test_demo_band_has_room_for_more_users_than_we_seed() -> None:
    """상한이 너무 빡빡하면 `--users` 를 늘리는 순간 대역을 넘습니다."""
    assert DEMO_USER_BASE + 1000 <= DEMO_USER_MAX


@pytest.mark.asyncio
async def test_zero_users_is_rejected_before_any_delete() -> None:
    """`--users 0 --apply` 는 기존 데모 행을 지우고 아무것도 넣지 않습니다.

    삭제가 먼저라 조용히 데모 데이터가 비워집니다. DB 를 열기 전에 막습니다.
    """
    with pytest.raises(ValueError, match="1 이상"):
        await run_demo_seed(users=0)


@pytest.mark.asyncio
async def test_users_beyond_the_demo_band_is_rejected() -> None:
    """상한을 넘으면 생성된 id 가 삭제 구간 밖으로 나가 다음 실행에서도 안 지워집니다."""
    with pytest.raises(ValueError, match="이하여야"):
        await run_demo_seed(users=DEMO_USER_MAX - DEMO_USER_BASE + 1)


def test_allowed_user_range_matches_the_delete_band() -> None:
    """허용 상한은 삭제 구간의 폭과 같아야 합니다.

    둘이 어긋나면 만들 수 있는데 지울 수 없는 사용자가 생깁니다. 경계값을 실제로
    생성해 보는 것은 100만 행이라 느려서, 상수 관계만 고정합니다.
    """
    assert DEMO_USER_MAX - DEMO_USER_BASE == 1_000_000
