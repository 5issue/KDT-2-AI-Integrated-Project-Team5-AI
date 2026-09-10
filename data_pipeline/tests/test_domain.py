"""도메인 상수 검증. DB 의 CHECK 제약과 어긋나면 적재가 터지므로 여기서 고정합니다."""

from __future__ import annotations

import pytest

from data_pipeline.domain import (
    SLOT_DERIVATION,
    STORAGE_SLOTS,
    TARGET_TABLE_CONTRACTS,
    derive_storage_columns,
    load_terminology,
)

# DB 의 ck_storage_guideline_* CHECK 제약에 적힌 값들
DB_SLOTS = {
    "pantry",
    "dop_pantry",
    "pantry_after_opening",
    "refrigerate",
    "dop_refrigerate",
    "refrigerate_after_opening",
    "refrigerate_after_thawing",
    "freeze",
    "dop_freeze",
}
DB_LOCATIONS = {"REFRIGERATOR", "FREEZER", "PANTRY"}
DB_CONTEXTS = {"FROM_PURCHASE", "AFTER_OPENING", "AFTER_THAWING", "NOT_APPLICABLE"}


def test_slots_match_db_check_constraint() -> None:
    """source_slot 후보가 DB CHECK 와 정확히 같아야 합니다."""
    assert set(STORAGE_SLOTS) == DB_SLOTS


def test_derived_values_are_within_db_check_constraints() -> None:
    """파생되는 location/context 도 CHECK 를 벗어나면 안 됩니다."""
    for slot in STORAGE_SLOTS:
        location, context = derive_storage_columns(slot)
        assert location in DB_LOCATIONS, slot
        assert context in DB_CONTEXTS, slot


def test_dop_slots_mean_from_purchase() -> None:
    """DOP 는 Date Of Purchase 입니다. 구매일 기준으로 파생되어야 합니다."""
    for slot in STORAGE_SLOTS:
        expected = "FROM_PURCHASE" if slot.startswith("dop_") else None
        if expected:
            assert derive_storage_columns(slot)[1] == expected, slot


def test_after_opening_and_thawing_are_mapped() -> None:
    """개봉 후 / 해동 후 슬롯이 맥락으로 옮겨져야 합니다."""
    assert derive_storage_columns("pantry_after_opening") == ("PANTRY", "AFTER_OPENING")
    assert derive_storage_columns("refrigerate_after_thawing") == ("REFRIGERATOR", "AFTER_THAWING")


def test_unknown_slot_is_rejected() -> None:
    """모르는 슬롯을 조용히 통과시키면 DB 에서 CHECK 위반으로 터집니다."""
    with pytest.raises(ValueError, match="허용되지 않은 source_slot"):
        derive_storage_columns("basement")


def test_derivation_covers_every_slot() -> None:
    """조회표에 빠진 슬롯이 없어야 합니다."""
    assert set(SLOT_DERIVATION) == set(STORAGE_SLOTS)


def test_terminology_includes_key_distinctions() -> None:
    """LLM 판단 기준이 프롬프트에 실제로 들어가는지 확인합니다."""
    guide = load_terminology(None)
    for keyword in ("재료", "푸드", "원재료", "성분", "농산", "축산", "수산", "냉장", "냉동"):
        assert keyword in guide, keyword


def test_terminology_can_be_overridden(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """원본 문서가 바뀌면 파일로 대체할 수 있어야 합니다."""
    path = tmp_path / "custom.md"
    path.write_text("커스텀 기준", encoding="utf-8")
    assert load_terminology(path) == "커스텀 기준"


def test_contracts_mention_actual_columns() -> None:
    """타깃 테이블 계약이 실제 스키마의 자연키를 담고 있어야 합니다."""
    assert "source_recipe_id" in TARGET_TABLE_CONTRACTS
    assert "ingredient_id" in TARGET_TABLE_CONTRACTS
    assert "ingredient_id2" not in TARGET_TABLE_CONTRACTS
