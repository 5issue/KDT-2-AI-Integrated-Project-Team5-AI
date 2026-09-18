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
DB_LOCATIONS = {"냉장", "냉동", "상온"}
DB_CONTEXTS = {"일반", "구매후", "개봉후", "해동후"}


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
        expected = "구매후" if slot.startswith("dop_") else None
        if expected:
            assert derive_storage_columns(slot)[1] == expected, slot


def test_after_opening_and_thawing_are_mapped() -> None:
    """개봉 후 / 해동 후 슬롯이 맥락으로 옮겨져야 합니다."""
    assert derive_storage_columns("pantry_after_opening") == ("상온", "개봉후")
    assert derive_storage_columns("refrigerate_after_thawing") == ("냉장", "해동후")


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


def test_match_key_collapses_spacing_variants() -> None:
    """같은 재료가 띄어쓰기만 달라 두 종으로 갈리면 3단계 호출이 늘고 적재가 빠집니다.

    331건 표본에서 실제로 `베이킹파우더`(26회) 와 `베이킹 파우더`(23회) 가 따로 잡혔습니다.
    """
    from data_pipeline.domain import ingredient_match_key

    assert ingredient_match_key("베이킹 파우더") == ingredient_match_key("베이킹파우더")
    assert ingredient_match_key("  다진 마늘  ") == "다진마늘"
    assert ingredient_match_key("Olive Oil") == "oliveoil"
    assert ingredient_match_key("") == ""


def test_match_key_is_shared_by_resolve_and_load() -> None:
    """staging 두 테이블이 이 값으로 조인합니다. 한쪽만 바뀌면 조인이 통째로 어긋납니다.

    `resolve` 가 패키지가 된 뒤로 `inspect.getsource` 는 `__init__.py` 만 돌려줍니다.
    그래서 패키지면 그 아래 .py 를 전부 훑습니다. 공용 키를 **어딘가에서** 쓰면 되고
    (`models` 처럼 안 쓰는 조각도 있습니다), 옛 정규화는 **어디에도** 없어야 합니다.
    """
    import inspect
    from pathlib import Path
    from types import ModuleType

    from data_pipeline.load import bulk_insert
    from data_pipeline.stages import resolve

    def sources(module: ModuleType) -> list[tuple[str, str]]:
        path = getattr(module, "__path__", None)
        if path is None:
            return [(module.__name__, inspect.getsource(module))]
        root = Path(next(iter(path)))
        return [(f"{module.__name__}.{f.stem}", f.read_text(encoding="utf-8")) for f in sorted(root.glob("*.py"))]

    for module in (bulk_insert, resolve):
        files = sources(module)
        uses_shared_key = any("ingredient_match_key(" in text for _, text in files)
        assert uses_shared_key, f"{module.__name__} 이 공용 키 함수를 쓰지 않습니다"
        for name, text in files:
            assert ".strip().lower()" not in text, f"{name} 에 옛 정규화가 남아 있습니다"


def test_duration_unit_singular_and_plural_collapse() -> None:
    """`Year` 3건과 `Years` 100건이 실제로 섞여 들어왔습니다. 같은 뜻이면 한 값이어야 합니다."""
    from data_pipeline.domain import normalize_duration_unit

    assert normalize_duration_unit("Year") == normalize_duration_unit("Years") == "년"
    assert normalize_duration_unit("Days") == "일"
    assert normalize_duration_unit("MONTHS") == "개월"
    assert normalize_duration_unit("Hours") == "시간"
    assert normalize_duration_unit("Weeks") == "주"


def test_duration_unit_keeps_unknown_and_empty_values() -> None:
    """모르는 단위를 임의로 바꾸지 않습니다. 빈 값은 null 로 모읍니다."""
    from data_pipeline.domain import normalize_duration_unit

    assert normalize_duration_unit("개월") == "개월"
    assert normalize_duration_unit("Servings") == "Servings"
    assert normalize_duration_unit("   ") is None
    assert normalize_duration_unit(None) is None


def test_cooking_method_collapses_spelling_variants() -> None:
    """`굽기` 하나에 표기가 여섯 갈래였습니다. 같은 뜻이면 한 값이어야 합니다."""
    from data_pipeline.domain import normalize_cooking_method

    variants = ("굽기", "오븐 굽기", "오븐구이", "오븐 구이", "구이", "그릴", "그릴하기", "로스팅", "베이킹")
    assert {normalize_cooking_method(value) for value in variants} == {"굽기"}


def test_cooking_method_takes_the_first_of_several() -> None:
    """한 칸에 여러 개를 넣은 값이 있습니다. varchar(50) 한 칸이라 하나만 골라야 합니다."""
    from data_pipeline.domain import normalize_cooking_method

    assert normalize_cooking_method("볶음, 조림, 오븐구이") == "볶음"
    assert normalize_cooking_method("끓이기, 시뮬머링") == "끓이기"


def test_cooking_method_prefers_the_appliance() -> None:
    """`에어프라이어구이` 는 굽기가 아니라 에어프라이어입니다. 버블이 기기로 걸러야 합니다."""
    from data_pipeline.domain import normalize_cooking_method

    assert normalize_cooking_method("에어프라이어구이") == "에어프라이어"
    assert normalize_cooking_method("에어프라이어, 바르기, 굽기") == "에어프라이어"


def test_cooking_method_scans_past_an_empty_word() -> None:
    """앞이 빈 말이고 뒤가 진짜인 값이 있습니다. 맨 앞만 보면 놓칩니다."""
    from data_pipeline.domain import normalize_cooking_method

    assert normalize_cooking_method("조리, 살짝 끓이기") == "끓이기"
    assert normalize_cooking_method("조리 (가열 및 혼합)") == "무침"


def test_cooking_method_drops_meaningless_values() -> None:
    """`조리`, `가열` 은 있으나 마나라 비웁니다. 화면과 필터 양쪽에서 걸리적거립니다."""
    from data_pipeline.domain import normalize_cooking_method

    assert normalize_cooking_method("조리") is None
    assert normalize_cooking_method("가열") is None
    assert normalize_cooking_method("팬 조리") is None
    assert normalize_cooking_method(None) is None


def test_cooking_method_results_are_all_declared() -> None:
    """결과가 `COOKING_METHODS` 밖으로 나가면 화면이 모르는 값을 받습니다."""
    from data_pipeline.domain import _METHOD_KEYWORDS, COOKING_METHODS

    assert {method for _, method in _METHOD_KEYWORDS} <= set(COOKING_METHODS)


def test_migration_keyword_table_matches_the_loader() -> None:
    """0009 마이그레이션과 적재기가 같은 규칙을 써야 합니다.

    하나는 이미 들어간 행을, 다른 하나는 앞으로 들어올 행을 고칩니다.
    둘이 갈라지면 적재 시점에 따라 같은 원문이 다른 값이 됩니다.
    """
    import importlib.util
    from pathlib import Path

    from data_pipeline.domain import _METHOD_KEYWORDS

    path = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "versions"
        / "0009_normalize_cooking_method.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.KEYWORDS == _METHOD_KEYWORDS
