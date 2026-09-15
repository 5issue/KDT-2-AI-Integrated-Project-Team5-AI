"""데모 시드 검증. DB 를 쓰지 않습니다.

여기서 잘못되면 추천 문구 실험의 입력이 통째로 어긋납니다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from data_pipeline.load.demo_seed import DEMO_USER_BASE, DemoReport, build_rows

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
