"""1단계 프로파일 제약 레이어 테스트.

여기 있는 케이스는 전부 실제 실행에서 LLM 이 틀렸던 것들입니다.
gpt-4o-mini 와 gpt-4.1-mini 로 두 번 돌려 두 번 다 같은 자리에서 어긋났습니다.
"""

from __future__ import annotations

from tests_helpers import write_parquet

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.config import Settings
from data_pipeline.schemas import DatasetProfile
from data_pipeline.stages import constraints


def make_profile(dataset: str, **overrides: object) -> DatasetProfile:
    """제약 검사에 필요한 필드만 채운 프로파일."""
    payload: dict[str, object] = {
        "dataset": dataset,
        "summary": "요약",
        "language": "ko",
        "target_tables": ["recipe"],
        "rows_per_entity": "many",
        "group_by_columns": [],
        "entity_key_columns": [],
        "content_columns": [],
        "companion_datasets": [],
        "loadable": True,
        "skip_reason": None,
        "column_meanings": [],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return DatasetProfile.model_validate(payload)


def test_drops_columns_that_do_not_exist(tmp_settings: Settings) -> None:
    """형제 스키마를 준 뒤로 다른 데이터셋 컬럼명을 끌어오는 환각이 나왔습니다."""
    write_parquet(tmp_settings.raw_dir / "wide.parquet", [{"ID": "1", "Name": "Butter"}])
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [make_profile("wide", group_by_columns=["product_id"], entity_key_columns=["product_id", "ID"])]
    adjusted, adjustments = constraints.apply(profiles, datasets)

    assert adjusted[0].group_by_columns == []
    assert adjusted[0].entity_key_columns == ["ID"]
    assert any(item.rule == "unknown_column" for item in adjustments)


def test_keeps_only_the_source_that_already_has_slot_values(tmp_settings: Settings) -> None:
    """같은 보관 데이터가 넓은 원본과 슬롯 단위 사본으로 둘 다 있으면 슬롯 쪽만 씁니다."""
    write_parquet(
        tmp_settings.raw_dir / "flat.parquet",
        [{"product_id": "fk_1", "storage": "refrigerate"}, {"product_id": "fk_1", "storage": "freeze"}],
    )
    write_parquet(tmp_settings.raw_dir / "wide.parquet", [{"ID": "1", "Refrigerate_Min": "1"}])
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [
        make_profile("flat", target_tables=["storage_guideline"]),
        make_profile("wide", target_tables=["storage_guideline"]),
    ]
    adjusted, adjustments = constraints.apply(profiles, datasets)
    by_name = {item.dataset: item for item in adjusted}

    assert by_name["flat"].target_tables == ["storage_guideline"]
    assert by_name["flat"].loadable is True
    assert by_name["wide"].target_tables == ["none"]
    assert by_name["wide"].loadable is False
    assert "flat" in (by_name["wide"].skip_reason or "")
    assert any(item.rule == "storage_source" for item in adjustments)


def test_recipe_ingredient_always_brings_recipe(tmp_settings: Settings) -> None:
    """recipe_ingredient 는 recipe_id 외래키가 필요해 단독으로 적재할 수 없습니다."""
    write_parquet(tmp_settings.raw_dir / "only.parquet", [{"recipe_name": "흰밥"}])
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [make_profile("only", target_tables=["recipe_ingredient"])]
    adjusted, adjustments = constraints.apply(profiles, datasets)

    assert adjusted[0].target_tables == ["recipe", "recipe_ingredient"]
    assert any(item.rule == "recipe_fk" for item in adjustments)


def test_companions_are_measured_not_guessed(tmp_settings: Settings) -> None:
    """컬럼 이름이 같아도 값이 안 겹치면 짝이 아닙니다. 영어 레시피와 한국 레시피가 그렇습니다."""
    write_parquet(
        tmp_settings.raw_dir / "ko_ingredients.parquet",
        [{"recipe_name": "흰밥", "원재료": "멥쌀"}, {"recipe_name": "누룽지", "원재료": "흰밥"}],
    )
    write_parquet(
        tmp_settings.raw_dir / "ko_steps.parquet",
        [{"recipe_name": "흰밥", "내용": "씻는다"}, {"recipe_name": "누룽지", "내용": "끓인다"}],
    )
    write_parquet(
        tmp_settings.raw_dir / "en_recipes.parquet",
        [{"recipe_name": "Apple Pie", "servings": "8"}, {"recipe_name": "Chicken Curry", "servings": "6"}],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    measured = constraints.find_companions(datasets)
    assert measured["ko_ingredients"] == ["ko_steps"]
    assert measured["ko_steps"] == ["ko_ingredients"]
    # 같은 recipe_name 컬럼이지만 값이 하나도 안 겹칩니다.
    assert measured["en_recipes"] == []


def test_verified_pair_is_loaded_even_when_llm_excluded_both(tmp_settings: Settings) -> None:
    """한 레시피가 두 표에 나뉜 경우 '반쪽이라 불완전' 으로 버리지 않습니다."""
    write_parquet(
        tmp_settings.raw_dir / "ko_ingredients.parquet",
        [{"recipe_name": "흰밥", "원재료": "멥쌀"}, {"recipe_name": "누룽지", "원재료": "흰밥"}],
    )
    write_parquet(
        tmp_settings.raw_dir / "ko_steps.parquet",
        [{"recipe_name": "흰밥", "내용": "씻는다"}, {"recipe_name": "누룽지", "내용": "끓인다"}],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    meanings = [{"column": "recipe_name", "meaning": "레시피 이름", "target_field": "recipe.name"}]
    profiles = [
        make_profile(
            "ko_ingredients",
            loadable=False,
            target_tables=["none"],
            group_by_columns=["recipe_name"],
            skip_reason="수량 단위 정보 없음",
            column_meanings=meanings,
        ),
        make_profile(
            "ko_steps",
            loadable=False,
            target_tables=["none"],
            group_by_columns=["recipe_name"],
            skip_reason="조리 단계만 있음",
        ),
    ]
    adjusted, adjustments = constraints.apply(profiles, datasets)
    by_name = {item.dataset: item for item in adjusted}

    assert by_name["ko_ingredients"].loadable is True
    assert by_name["ko_ingredients"].target_tables == ["recipe", "recipe_ingredient"]
    assert by_name["ko_ingredients"].skip_reason is None
    # 짝이지만 recipe 컬럼 대응이 없는 쪽은 건드리지 않습니다.
    assert by_name["ko_steps"].loadable is False
    assert any(item.rule == "companion_pair" for item in adjustments)


def test_duplicate_copies_are_grouped_by_columns_and_row_count(tmp_settings: Settings) -> None:
    """타입만 다른 사본을 잡아냅니다. 실제 raw 에 661행짜리 사본 두 개가 있었습니다.

    `discover_datasets` 는 스키마 지문에 타입을 넣어 이 둘을 별개 데이터셋으로 둡니다
    (그래야 같은 내용이 한 덩어리로 합쳐지지 않습니다). 사본 판정은 그 뒤 단계입니다.
    """
    write_parquet(tmp_settings.raw_dir / "numeric.parquet", [{"ID": 1, "Name": "Butter"}])
    write_parquet(tmp_settings.raw_dir / "text.parquet", [{"ID": "1", "Name": "Butter"}])
    write_parquet(tmp_settings.raw_dir / "other.parquet", [{"recipe_name": "흰밥"}])

    datasets = discover_datasets(tmp_settings.raw_dir)
    assert {item.name for item in datasets} == {"numeric", "text", "other"}
    assert constraints.find_duplicate_groups(datasets) == [["numeric", "text"]]

    # 사본은 짝이 아닙니다. 값이 겹쳐도 companion 으로 잡히면 안 됩니다.
    assert constraints.find_companions(datasets)["numeric"] == []


def test_no_adjustment_leaves_profiles_untouched(tmp_settings: Settings) -> None:
    """제약에 걸릴 게 없으면 LLM 판단을 그대로 둡니다."""
    write_parquet(tmp_settings.raw_dir / "clean.parquet", [{"recipe_name": "흰밥"}])
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [make_profile("clean", target_tables=["recipe"], entity_key_columns=["recipe_name"])]
    adjusted, adjustments = constraints.apply(profiles, datasets)

    assert adjustments == []
    assert adjusted[0] == profiles[0]
    assert constraints.render([]) == "제약 조정 없음 (LLM 판단 그대로)"


def test_surrogate_id_overlap_is_not_a_companion(tmp_settings: Settings) -> None:
    """서로 다른 표의 ID 는 각자 1부터 세는 별개 번호라 겹침이 무조건 1.0 입니다."""
    write_parquet(
        tmp_settings.raw_dir / "products.parquet", [{"ID": "1", "Name": "Butter"}, {"ID": "2", "Name": "Milk"}]
    )
    write_parquet(tmp_settings.raw_dir / "categories.parquet", [{"ID": "1", "Category_Name": "Dairy"}])

    measured = constraints.find_companions(discover_datasets(tmp_settings.raw_dir))
    assert measured["products"] == []
    assert measured["categories"] == []


def test_companion_pair_needs_a_shared_grouping_key(tmp_settings: Settings) -> None:
    """recipe 필드를 하나 갖고 우연히 겹치는 데이터가 적재로 끌려 올라오면 안 됩니다."""
    write_parquet(
        tmp_settings.raw_dir / "methods.parquet",
        [{"Cooking_Method": "Oven", "Note": "굽기"}, {"Cooking_Method": "Grill", "Note": "직화"}],
    )
    write_parquet(
        tmp_settings.raw_dir / "notes.parquet",
        [{"Note": "굽기", "Extra": "a"}, {"Note": "직화", "Extra": "b"}],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    meanings = [{"column": "Cooking_Method", "meaning": "조리 방법", "target_field": "recipe.cooking_method"}]
    profiles = [
        make_profile(
            "methods",
            loadable=False,
            target_tables=["none"],
            group_by_columns=["Cooking_Method"],
            column_meanings=meanings,
        ),
        make_profile("notes", loadable=False, target_tables=["none"]),
    ]
    adjusted, _ = constraints.apply(profiles, datasets)
    by_name = {item.dataset: item for item in adjusted}

    # Note 로 겹치긴 하지만 methods 의 그룹 키(Cooking_Method)는 notes 에 없습니다.
    assert by_name["methods"].loadable is False


def test_companion_group_extracts_from_one_side_only(tmp_settings: Settings) -> None:
    """짝 양쪽이 loadable 이면 같은 엔티티가 두 번 추출돼 recipe 가 두 배로 생깁니다."""
    write_parquet(
        tmp_settings.raw_dir / "ko_ingredients.parquet",
        [{"recipe_name": "흰밥", "원재료": "멥쌀"}, {"recipe_name": "누룽지", "원재료": "흰밥"}],
    )
    write_parquet(
        tmp_settings.raw_dir / "ko_steps.parquet",
        [{"recipe_name": "흰밥", "내용": "씻는다"}, {"recipe_name": "누룽지", "내용": "끓인다"}],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    meanings = [{"column": "recipe_name", "meaning": "레시피 이름", "target_field": "recipe.name"}]
    profiles = [
        make_profile(
            "ko_ingredients",
            target_tables=["recipe", "recipe_ingredient"],
            group_by_columns=["recipe_name"],
            column_meanings=meanings,
        ),
        make_profile(
            "ko_steps",
            target_tables=["recipe", "recipe_ingredient"],
            group_by_columns=["recipe_name"],
            column_meanings=meanings,
        ),
    ]
    adjusted, adjustments = constraints.apply(profiles, datasets)
    by_name = {item.dataset: item for item in adjusted}

    # 대응 수가 같으면 이름순으로 결정적으로 고릅니다.
    assert by_name["ko_ingredients"].loadable is True
    assert by_name["ko_steps"].loadable is False
    assert "ko_ingredients" in (by_name["ko_steps"].skip_reason or "")
    # 짝 관계 자체는 남아 있어야 2단계가 companion 으로 끌어옵니다.
    assert by_name["ko_steps"].companion_datasets == ["ko_ingredients"]
    assert any(item.rule == "companion_primary" for item in adjustments)


def test_companion_group_untouched_when_targets_differ(tmp_settings: Settings) -> None:
    """타깃 테이블이 겹치지 않으면 중복 적재가 아니므로 건드리지 않습니다."""
    write_parquet(
        tmp_settings.raw_dir / "left.parquet",
        [{"key": "흰밥", "storage": "refrigerate"}, {"key": "누룽지", "storage": "freeze"}],
    )
    write_parquet(
        tmp_settings.raw_dir / "right.parquet",
        [{"key": "흰밥", "원재료": "멥쌀"}, {"key": "누룽지", "원재료": "흰밥"}],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [
        make_profile("left", target_tables=["storage_guideline"], group_by_columns=["key"]),
        make_profile("right", target_tables=["recipe"], group_by_columns=["key"]),
    ]
    adjusted, _ = constraints.apply(profiles, datasets)
    by_name = {item.dataset: item for item in adjusted}

    assert by_name["left"].loadable is True
    assert by_name["right"].loadable is True


def test_slot_column_is_removed_from_grouping_key(tmp_settings: Settings) -> None:
    """2단계 스키마는 품목 하나에 슬롯 여러 개를 묶어 받습니다(rules 리스트).

    슬롯을 그룹 키에 넣으면 한 품목이 슬롯 수만큼 쪼개져 요청이 배로 늘어납니다.
    실제로 storage_guide 가 651품목 대신 1298건으로 갈렸습니다.
    """
    write_parquet(
        tmp_settings.raw_dir / "flat.parquet",
        [
            {"product_id": "fk_1", "storage": "refrigerate", "tips": "a"},
            {"product_id": "fk_1", "storage": "freeze", "tips": "b"},
            {"product_id": "fk_2", "storage": "pantry", "tips": "c"},
        ],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [
        make_profile(
            "flat",
            target_tables=["storage_guideline"],
            group_by_columns=["product_id", "storage"],
        )
    ]
    adjusted, adjustments = constraints.apply(profiles, datasets)

    assert adjusted[0].group_by_columns == ["product_id"]
    assert any(item.rule == "slot_not_a_key" for item in adjustments)


def test_grouping_key_without_slot_is_left_alone(tmp_settings: Settings) -> None:
    """이미 품목 단위로 묶여 있으면 건드리지 않습니다."""
    write_parquet(
        tmp_settings.raw_dir / "flat.parquet",
        [{"product_id": "fk_1", "storage": "refrigerate"}, {"product_id": "fk_2", "storage": "freeze"}],
    )
    datasets = discover_datasets(tmp_settings.raw_dir)

    profiles = [make_profile("flat", target_tables=["storage_guideline"], group_by_columns=["product_id"])]
    adjusted, adjustments = constraints.apply(profiles, datasets)

    assert adjusted[0].group_by_columns == ["product_id"]
    assert not any(item.rule == "slot_not_a_key" for item in adjustments)
