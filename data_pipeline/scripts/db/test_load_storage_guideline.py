"""보관 지침 적재기의 선별 규칙과 안전장치 테스트입니다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.db._env import validate_confirmation
from scripts.db.load_storage_guideline import (
    PRODUCTION_CONFIRMATION,
    Guideline,
    apply_child_rules,
    drop_processed_from_raw,
    select_representatives,
    validate_enums,
)


def guideline(
    *,
    ingredient_id: int = 1,
    location: str = "냉장",
    context: str = "일반",
    source: str = "Chicken",
    subtitle: str | None = None,
    slot: str = "refrigerate",
    duration: tuple[str, str, str] | None = ("1", "2", "일"),
    text: str = "1-2일",
) -> Guideline:
    """테스트용 지침 한 줄을 만듭니다."""
    minimum, maximum, unit = duration if duration else (None, None, None)
    return Guideline(
        ingredient_id=ingredient_id,
        storage_location=location,
        storage_context=context,
        source_food_name=source,
        source_food_subtitle=subtitle,
        source_slot=slot,
        duration_min=Decimal(minimum) if minimum else None,
        duration_max=Decimal(maximum) if maximum else None,
        duration_unit=unit,
        duration_text=text,
        storage_tips=None,
    )


def test_unique_key_is_accepted_as_is() -> None:
    """자연키가 하나뿐인 행은 그대로 적재 대상입니다."""
    accepted, decisions = select_representatives([guideline()])

    assert len(accepted) == 1
    assert decisions == []


def test_same_duration_collapses_to_one_row() -> None:
    """기간이 같으면 표기만 다른 것이므로 대표 1건으로 접습니다."""
    rows = [
        guideline(source="Rice", text="1-2 일"),
        guideline(source="Cornmeal", text="1-2일"),
    ]

    accepted, decisions = select_representatives(rows)

    assert len(accepted) == 1
    assert decisions == []


def test_conflicting_duration_picks_the_shortest_and_records_it() -> None:
    """기간이 다르면 일 단위로 환산해 가장 짧은 쪽을 고르고, 고른 사실을 남깁니다."""
    rows = [
        guideline(source="Rice", duration=("1", "1", "년"), text="1년"),
        guideline(source="Cornmeal", duration=("2", "4", "개월"), text="2-4개월"),
        guideline(source="Unknown", duration=None, text="기간 정보 없음"),
    ]

    accepted, decisions = select_representatives(rows)

    assert [row.source_food_name for row in accepted] == ["Cornmeal"]
    assert len(decisions) == 1
    assert decisions[0].row_count == 3
    assert decisions[0].chosen_duration == "2-4개월"
    assert decisions[0].others == ["Rice: 1년", "Unknown: 기간 정보 없음"]


def test_child_rule_wins_over_a_wrong_original_mapping() -> None:
    """부위 규칙은 원천 단위로 검토한 것이라, 소고기 원천이 돼지고기에 붙어 있어도 규칙대로 옮깁니다."""
    rules = {("Beef", "short ribs"): "K:소고기:SMALL:갈비"}
    beef_on_pork = guideline(ingredient_id=404, source="Beef", subtitle="short ribs")
    other = guideline(ingredient_id=404, source="Pork", subtitle="ground")

    rows, moved = apply_child_rules([beef_on_pork, other], rules, {"K:소고기:SMALL:갈비": 1037})

    assert moved == 1
    assert [row.ingredient_id for row in rows] == [1037, 404]


def test_processed_sources_are_dropped_only_from_raw_ingredients() -> None:
    """생닭에서는 너겟 지침을 빼고, 원래 가공품인 햄은 가공 원천을 그대로 씁니다."""
    processed = {("Chicken nuggets, patties", ""), ("Ham", "cooked")}
    rows = [
        guideline(ingredient_id=401, source="Chicken nuggets, patties"),
        guideline(ingredient_id=401, source="Chicken", subtitle="whole"),
        guideline(ingredient_id=900, source="Ham", subtitle="cooked"),
    ]

    kept, dropped = drop_processed_from_raw(rows, processed, raw_ingredients={401})

    assert dropped == 1
    assert [row.source_food_name for row in kept] == ["Chicken", "Ham"]


def test_child_rule_splits_a_conflict_before_picking() -> None:
    """부위로 옮기면 충돌이 풀려 두 부위가 각자 자기 기간을 갖습니다."""
    rules = {("Pork", "tenderloin"): "K:SMALL:안심"}
    rows = [
        guideline(ingredient_id=404, source="Pork", subtitle="tenderloin", duration=("3", "5", "일")),
        guideline(ingredient_id=404, source="Pork", subtitle="ground", duration=("1", "2", "일")),
    ]

    moved_rows, _ = apply_child_rules(rows, rules, {"K:SMALL:안심": 1034})
    accepted, decisions = select_representatives(moved_rows)

    assert sorted(row.ingredient_id for row in accepted) == [404, 1034]
    assert decisions == []


def test_representative_is_stable_across_runs() -> None:
    """입력 순서가 달라도 같은 대표가 뽑혀야 재적재가 멱등합니다."""
    first = guideline(source="Apples")
    second = guideline(source="Zucchini")

    forward, _ = select_representatives([first, second])
    backward, _ = select_representatives([second, first])

    assert forward[0].source_food_name == backward[0].source_food_name == "Apples"


def test_locations_and_contexts_are_independent_keys() -> None:
    """장소나 상황이 다르면 서로 다른 조합이므로 충돌이 아닙니다."""
    rows = [
        guideline(location="냉장", duration=("1", "2", "일")),
        guideline(location="냉동", duration=("6", "8", "개월")),
        guideline(context="개봉후", duration=("3", "5", "일")),
    ]

    accepted, decisions = select_representatives(rows)

    assert len(accepted) == 3
    assert decisions == []


def test_enum_values_are_checked_before_loading() -> None:
    """0013 의 CHECK 와 같은 허용값을 적재 전에 확인합니다."""
    validate_enums([guideline()])

    with pytest.raises(ValueError, match="storage_location"):
        validate_enums([guideline(location="REFRIGERATOR")])
    with pytest.raises(ValueError, match="storage_context"):
        validate_enums([guideline(context="NOT_APPLICABLE")])
    with pytest.raises(ValueError, match="duration_unit"):
        validate_enums([guideline(duration=("1", "2", "Days"))])


def test_null_duration_unit_is_allowed() -> None:
    """기간을 모르는 행은 단위가 비어 있어도 됩니다."""
    validate_enums([guideline(duration=None, text="기간 정보 없음")])


def test_production_apply_requires_exact_confirmation() -> None:
    """Production 쓰기에는 확인 문자열이 필요합니다."""
    with pytest.raises(ValueError, match="Production 적용"):
        validate_confirmation("production", True, None, token=PRODUCTION_CONFIRMATION)
    validate_confirmation("production", True, PRODUCTION_CONFIRMATION, token=PRODUCTION_CONFIRMATION)


def test_dry_run_does_not_require_confirmation() -> None:
    """읽기만 하는 dry-run 은 확인 문자열 없이 돕니다."""
    validate_confirmation("production", False, None, token=PRODUCTION_CONFIRMATION)
    validate_confirmation("local", False, None, token=PRODUCTION_CONFIRMATION)
