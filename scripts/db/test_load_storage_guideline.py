"""보관 지침 적재기의 선별 규칙과 안전장치 테스트입니다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.db.load_storage_guideline import (
    PRODUCTION_CONFIRMATION,
    Guideline,
    select_representatives,
    validate_confirmation,
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
    accepted, held = select_representatives([guideline()])

    assert len(accepted) == 1
    assert held == []


def test_same_duration_collapses_to_one_row() -> None:
    """기간이 같으면 표기만 다른 것이므로 대표 1건으로 접습니다."""
    rows = [
        guideline(source="Rice", text="1-2 일"),
        guideline(source="Cornmeal", text="1-2일"),
    ]

    accepted, held = select_representatives(rows)

    assert len(accepted) == 1
    assert held == []


def test_conflicting_duration_is_held_not_merged() -> None:
    """기간이 다르면 적재하지 않고 원천과 기간을 남깁니다. 최소값을 고르지 않습니다."""
    rows = [
        guideline(source="Cornmeal", duration=("2", "4", "개월"), text="2-4개월"),
        guideline(source="Rice", duration=("1", "1", "년"), text="1년"),
    ]

    accepted, held = select_representatives(rows)

    assert accepted == []
    assert len(held) == 1
    assert held[0].row_count == 2
    assert held[0].sources == ["Cornmeal", "Rice"]
    assert held[0].durations == ["1년", "2-4개월"]


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

    accepted, held = select_representatives(rows)

    assert len(accepted) == 3
    assert held == []


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
        validate_confirmation("production", True, None)
    validate_confirmation("production", True, PRODUCTION_CONFIRMATION)


def test_dry_run_does_not_require_confirmation() -> None:
    """읽기만 하는 dry-run 은 확인 문자열 없이 돕니다."""
    validate_confirmation("production", False, None)
    validate_confirmation("local", False, None)
