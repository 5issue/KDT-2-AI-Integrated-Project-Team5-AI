"""재료 마스터 정렬 계획 테스트입니다."""

from __future__ import annotations

import pytest

from scripts.db.align_ingredient_master import plan


def action(kind: str, key: str, name: str, parent: str = "") -> dict[str, str]:
    """정렬 설정 한 줄을 만듭니다."""
    return {"action": kind, "source_identity_key": key, "name": name, "parent_source_identity_key": parent}


def test_only_pending_actions_are_planned() -> None:
    """이미 있는 INSERT 와 이미 바뀐 RENAME 은 다시 하지 않습니다."""
    existing = {"K:고추": (131, "고추"), "K:김": (946, "조미김"), "K:부침": (907, "부침가루/튀김가루/믹스")}
    actions = [
        action("INSERT", "K:고추:꽈리고추", "꽈리고추", parent="K:고추"),
        action("INSERT", "K:고추", "고추"),
        action("RENAME", "K:김", "조미김"),
        action("RENAME", "K:부침", "부침가루"),
    ]

    assert [row["name"] for row in plan(actions, existing)] == ["꽈리고추", "부침가루"]


def test_missing_parent_or_rename_target_stops_the_plan() -> None:
    """부모가 없거나 이름을 바꿀 대상이 없으면 아무것도 쓰지 않고 멈춥니다."""
    with pytest.raises(ValueError, match="부모"):
        plan([action("INSERT", "K:고추:꽈리고추", "꽈리고추", parent="K:고추")], {})
    with pytest.raises(ValueError, match="이름을 바꿀"):
        plan([action("RENAME", "K:김", "조미김")], {})
