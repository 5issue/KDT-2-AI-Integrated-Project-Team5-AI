"""데모 시나리오 시드의 설정 읽기와 불변식 테스트입니다.

DB 없이 검증할 수 있는 것만 여기서 봅니다. 원천 키 조회와 검증 7항목은 실제 스키마가
필요해 통합 검증으로 돌립니다.
"""

from __future__ import annotations

import pytest

from data_pipeline.load.demo_scenario import (
    PRODUCTS_CSV,
    RECIPES_CSV,
    ROLE_FRIDGE,
    ROLE_MISSING,
    ScenarioReport,
    config_version,
    load_products,
    load_recipes,
    purchase_flow_recipe,
)


def test_config_files_are_readable() -> None:
    """설정 두 벌이 실제로 읽히고 비어 있지 않습니다."""
    recipes = load_recipes()
    products = load_products()

    assert len(recipes) == 6
    assert [product.role for product in products].count(ROLE_FRIDGE) == 4
    assert [product.role for product in products].count(ROLE_MISSING) == 2


def test_exactly_one_purchase_flow_recipe() -> None:
    """구매 흐름 레시피가 둘이면 어느 쪽에 우선순위를 달지 알 수 없습니다."""
    target = purchase_flow_recipe(load_recipes())

    assert target.is_purchase_flow
    assert target.expected_name == "된장 두부찌개"


def test_purchase_flow_must_not_be_ambiguous() -> None:
    """0개나 2개면 멈춥니다."""
    recipes = load_recipes()
    only_flags = [recipe for recipe in recipes if recipe.is_purchase_flow]

    with pytest.raises(ValueError, match="정확히 하나"):
        purchase_flow_recipe([recipe for recipe in recipes if not recipe.is_purchase_flow])
    with pytest.raises(ValueError, match="정확히 하나"):
        purchase_flow_recipe(only_flags * 2)


def test_fridge_products_carry_expected_ingredient() -> None:
    """상품마다 어떤 재료여야 하는지 설정에 적혀 있어야 검증이 가능합니다."""
    for product in load_products():
        assert product.expected_ingredient, product.source_product_id
        assert product.expected_name, product.source_product_id


def test_hierarchy_is_declared_only_where_needed() -> None:
    """부모를 적은 행은 목살 하나뿐입니다. 나머지는 계층이 필요 없습니다."""
    with_parent = [product for product in load_products() if product.expected_parent_ingredient]

    assert len(with_parent) == 1
    assert with_parent[0].expected_ingredient == "목심"
    assert with_parent[0].expected_parent_ingredient == "돼지고기"


def test_config_version_changes_with_content(tmp_path) -> None:
    """어떤 설정으로 돌렸는지 결과에 남도록 내용 지문을 만듭니다."""
    first = tmp_path / "a.csv"
    first.write_text("a", encoding="utf-8")
    before = config_version(first)
    first.write_text("b", encoding="utf-8")

    assert before != config_version(first)
    assert config_version(RECIPES_CSV, PRODUCTS_CSV) == config_version(PRODUCTS_CSV, RECIPES_CSV)


def test_report_is_not_ok_when_a_check_fails() -> None:
    """검증이 하나라도 실패하면 전체가 실패입니다."""
    report = ScenarioReport(checks=[("A", True, ""), ("B", False, "이유")])

    assert not report.ok
    assert "[실패] B — 이유" in report.render()
    assert "dry-run" in report.render()


def test_report_does_not_leak_connection_info() -> None:
    """결과에는 접속 정보를 담지 않습니다."""
    report = ScenarioReport(config_version="abc123", applied_at="2026-09-21T00:00:00+00:00", applied=True)
    rendered = report.render()

    assert "postgres" not in rendered.lower()
    assert "abc123" in rendered
