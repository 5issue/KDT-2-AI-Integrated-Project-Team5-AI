"""별칭 정리 계획 테스트입니다."""

from __future__ import annotations

from scripts.db.reset_ingredient_aliases import plan


def test_only_reviewed_aliases_remain_and_removed_ones_are_reported() -> None:
    """검토된 별칭만 남기고, 지운 값은 리포트용으로 돌려줍니다. 이미 맞는 행은 건드리지 않습니다."""
    reviewed = {"K:전분": [], "K:달걀": ["계란"]}
    rows = [
        (11, "전분", "K:전분", ["감자", "고구마", "쌀"]),
        (20, "달걀", "K:달걀", ["계란"]),
        (30, "소금", "K:소금", []),
    ]

    changes = plan(rows, reviewed)

    assert changes == [(11, "전분", [], ["감자", "고구마", "쌀"])]
