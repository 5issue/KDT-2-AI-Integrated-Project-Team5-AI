"""상품 이미지 백필의 식별자 추출과 갱신 계획 테스트입니다."""

from __future__ import annotations

import json

import pytest

from scripts.db.backfill_product_images import (
    PRODUCTION_CONFIRMATION,
    ProductRow,
    load_cache,
    pick_image,
    plan_updates,
    validate_confirmation,
)


def test_page_url_wins_over_source_product_id() -> None:
    """variant 행은 source_product_id 가 deal 번호라 페이지 주소를 먼저 봐야 합니다."""
    row = ProductRow(1164, "https://www.kurly.com/goods/5153238", "10153238", None)

    assert row.goods_no == "5153238"


def test_source_product_id_is_the_fallback_without_a_url() -> None:
    """주소가 없는 행에서만 물러섭니다. 틀리면 404 로 떨어져 비워 둡니다."""
    assert ProductRow(3302, None, "5027766", None).goods_no == "5027766"
    assert ProductRow(3302, "https://www.kurly.com/", "5027766", None).goods_no == "5027766"


def test_product_without_any_key_is_skipped() -> None:
    """열쇠가 하나도 없으면 받을 곳이 없습니다. 지어내지 않습니다."""
    assert ProductRow(3302, None, None, None).goods_no is None
    assert ProductRow(3302, None, "SKU-A", None).goods_no is None


def test_variants_of_one_page_share_the_image() -> None:
    """한 페이지의 variant 여러 행에 같은 대표 이미지가 들어갑니다."""
    page = "https://www.kurly.com/goods/5049247"
    products = [ProductRow(1, page, None, None), ProductRow(2, page, None, None)]

    updates = plan_updates(products, {"5049247": "https://img/1.jpg"})

    assert updates == [(1, "https://img/1.jpg"), (2, "https://img/1.jpg")]


def test_unchanged_rows_are_not_rewritten() -> None:
    """같은 값을 다시 쓰지 않습니다. 재실행이 0건이어야 멱등입니다."""
    page = "https://www.kurly.com/goods/5049247"
    products = [ProductRow(1, page, None, "https://img/1.jpg"), ProductRow(2, page, None, "https://img/old.jpg")]

    updates = plan_updates(products, {"5049247": "https://img/1.jpg"})

    assert updates == [(2, "https://img/1.jpg")]


def test_missing_image_leaves_the_row_alone() -> None:
    """받지 못한 페이지의 행은 건드리지 않습니다."""
    products = [ProductRow(1, "https://www.kurly.com/goods/999", None, "https://img/old.jpg")]

    assert plan_updates(products, {}) == []


def test_image_field_priority() -> None:
    """대표 이미지가 있으면 그것을, 없으면 다음 후보를 씁니다."""
    assert pick_image({"data": {"main_image_url": "https://a", "share_image_url": "https://b"}}) == "https://a"
    assert pick_image({"data": {"share_image_url": "https://b"}}) == "https://b"
    assert pick_image({"data": {"main_image_url": None}}) is None
    assert pick_image({"data": {"main_image_url": "/relative.jpg"}}) is None


def test_cache_lets_a_rerun_skip_the_network(tmp_path) -> None:
    """받아 둔 페이지는 다시 받지 않습니다. 중간에 끊겨도 이어서 받습니다."""
    path = tmp_path / "images.jsonl"
    path.write_text(
        json.dumps({"goods_no": "5049247", "image_url": "https://img/1.jpg"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert load_cache(path) == {"5049247": "https://img/1.jpg"}
    assert load_cache(tmp_path / "none.jsonl") == {}


def test_production_apply_requires_exact_confirmation() -> None:
    """Production 쓰기에는 확인 문자열이 필요합니다."""
    with pytest.raises(ValueError, match="Production 적용"):
        validate_confirmation("production", True, None)
    validate_confirmation("production", True, PRODUCTION_CONFIRMATION)
    validate_confirmation("production", False, None)
