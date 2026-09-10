"""상품 카탈로그 적재 변환 테스트. DB 없이 순수 변환만 확인합니다."""

from __future__ import annotations

import json
from pathlib import Path

from tests_helpers import write_parquet

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.load import catalog


def category_row(name: str, parent_path: str, depth: int, **extra: object) -> dict[str, object]:
    """category_raw 한 행."""
    row: dict[str, object] = {
        "category_type": "FOOD",
        "name": name,
        "parent_path": parent_path,
        "depth": str(depth),
        "source": "KURLY_CRAWL",
        "source_code": name,
        "is_edible": "True",
        "metadata": "{}",
    }
    row.update(extra)
    return row


def product_row(pid: str, name: str, **extra: object) -> dict[str, object]:
    """product_raw 한 행. 정리본만 가진 컬럼을 포함합니다."""
    row: dict[str, object] = {
        "source_type": "KURLY_CRAWL",
        "source_product_id": pid,
        "name": name,
        "price": "6980",
        "product_type": "RAW_MATERIAL",
        "category_path": "수산 > 새우",
        "storage_type": "FROZEN",
        "origin_country": "베트남산",
        "weight_g": "200",
        "unit_count": "1",
        "sku": "None",
        "stock_quantity": "None",
        "image_url": "None",
        "ingredients": "[]",
        "metadata": "{}",
    }
    row.update(extra)
    return row


def test_category_path_becomes_the_parent_key(tmp_path: Path) -> None:
    """raw 에는 parent_id 가 없습니다. 경로로 부모를 찾을 수 있어야 합니다."""
    write_parquet(
        tmp_path / "category_raw.parquet",
        [category_row("수산", "", 0), category_row("새우", "수산", 1)],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    paths = [row[0] for row in rows.categories]
    assert paths == ["수산", "수산 > 새우"]
    assert rows.categories[1][1] == "수산", "부모 경로가 그대로 실려야 SQL 이 부모를 찾습니다"


def test_raw_crawl_and_cleaned_product_are_not_loaded_twice(tmp_path: Path) -> None:
    """원본 크롤과 정리본이 함께 들어옵니다. 느슨하게 잡으면 같은 상품이 두 번 적재됩니다.

    실제로 2,553건이 5,106건으로 부풀었습니다.
    """
    write_parquet(tmp_path / "product_raw.parquet", [product_row("5059084", "새우살 200g")])
    # 원본 크롤: source_type/source_product_id/name/price 는 있지만 정리본 컬럼이 없습니다.
    write_parquet(
        tmp_path / "kurly_products_food.parquet",
        [
            {
                "source_type": "KURLY",
                "source_product_id": "5059084",
                "name": "새우살 200g",
                "price": "6980",
                "categories": '["수산"]',
                "category_root": "수산",
            }
        ],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    assert len(rows.products) == 1
    assert rows.products[0][0] == "KURLY_CRAWL"


def test_parquet_none_strings_become_null(tmp_path: Path) -> None:
    """parquet 이 빈 값을 'None' 문자열로 싣고 옵니다. 그대로 넣으면 sku UNIQUE 가 터집니다."""
    write_parquet(tmp_path / "product_raw.parquet", [product_row("1", "가"), product_row("2", "나")])
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    # 컬럼 순서상 sku 는 10번째, stock_quantity 는 11번째
    assert all(row[10] is None for row in rows.products)
    assert all(row[11] is None for row in rows.products)


def test_product_ingredients_are_normalized_to_match_key(tmp_path: Path) -> None:
    """구성 재료는 3단계 매칭 키와 같은 규칙이라야 조인됩니다."""
    write_parquet(
        tmp_path / "product_raw.parquet",
        [
            product_row(
                "1",
                "양념 돼지불고기",
                ingredients=json.dumps(
                    [{"name": "돼지 고기", "role": "PRIMARY", "ratio": "0.7"}, {"name": "양파"}],
                    ensure_ascii=False,
                ),
            )
        ],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    # 컬럼: source_type, source_product_id, normalized_name, ingredient_id, role, ...
    assert [(row[2], row[4]) for row in rows.product_ingredients] == [("돼지고기", "PRIMARY"), ("양파", "PRIMARY")]
    assert all(row[3] is None for row in rows.product_ingredients), "raw 로 온 것은 3단계가 해석합니다"


def test_products_without_price_are_skipped(tmp_path: Path) -> None:
    """price 는 NOT NULL 입니다. 없는 행을 넣으면 적재 전체가 롤백됩니다."""
    write_parquet(
        tmp_path / "product_raw.parquet",
        [product_row("1", "가", price="None"), product_row("2", "나")],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    assert len(rows.products) == 1
    assert rows.skipped_products == 1


def test_render_warns_when_loop_would_break(tmp_path: Path) -> None:
    """구성 재료가 비면 MVP 루프가 끊깁니다. 조용히 넘어가면 안 됩니다."""
    write_parquet(tmp_path / "product_raw.parquet", [product_row("1", "가")])
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    assert "루프가 끊깁니다" in catalog.render(rows)


def test_nan_in_metadata_is_nulled(tmp_path: Path) -> None:
    """파이썬 json 은 NaN 을 뱉지만 PostgreSQL JSON 은 거부합니다.

    크롤 데이터의 빈 수치가 NaN 으로 들어와 적재가 통째로 롤백됐습니다.
    """
    write_parquet(
        tmp_path / "product_raw.parquet",
        [product_row("1", "가", metadata='{"review_count": NaN, "brand": "청정원"}')],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    meta = json.loads(rows.products[0][12])
    assert meta["review_count"] is None
    assert meta["brand"] == "청정원"
    assert "NaN" not in rows.products[0][12]


def test_duplicate_sku_keeps_the_first_and_nulls_the_rest(tmp_path: Path) -> None:
    """sku 는 UNIQUE 입니다. 원본에 중복이 있어 적재 전체가 롤백됐습니다(1551개 중 103개).

    상품은 (source_type, source_product_id)로 구분되므로 행은 살리고 sku 만 버립니다.
    """
    write_parquet(
        tmp_path / "product_raw.parquet",
        [
            product_row("1", "가", sku="M0001"),
            product_row("2", "나", sku="M0001"),
            product_row("3", "다", sku="M0002"),
        ],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))

    skus = [row[10] for row in rows.products]
    assert skus == ["M0001", None, "M0002"]
    assert len(rows.products) == 3, "중복 sku 때문에 상품 행이 사라지면 안 됩니다"


def test_derives_ingredient_from_product_name(tmp_path: Path) -> None:
    """raw 의 ingredients 가 2,553건 전부 비어 있어 product_ingredient 를 못 만들었습니다.

    상품명에 들어 있는 마스터 재료명 중 가장 긴 것을 씁니다.
    """
    write_parquet(
        tmp_path / "product_raw.parquet",
        [product_row("1", "[피쉬쉘] 자숙 칵테일 새우살 200g (냉동)", category_path="수산 > 새우")],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))
    derived = catalog.derive_product_ingredients(rows, {"새우": 7, "새우살": 9})

    assert derived == 1
    # 더 긴 `새우살` 이 이깁니다.
    assert rows.product_ingredients[0][2] == "새우살"
    assert rows.product_ingredients[0][3] == 9


def test_falls_back_to_category_leaf(tmp_path: Path) -> None:
    """상품명에서 못 찾으면 카테고리 잎 이름으로 봅니다."""
    write_parquet(
        tmp_path / "product_raw.parquet",
        [product_row("1", "[르 아뜰리에] 게랑드 토판", category_path="양념 > 소금")],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))
    catalog.derive_product_ingredients(rows, {"소금": 3})

    assert rows.product_ingredients[0][2] == "소금"


def test_one_character_ingredients_do_not_match_everything(tmp_path: Path) -> None:
    """`물` 같은 한 글자 재료는 아무 상품명에나 걸립니다."""
    write_parquet(tmp_path / "product_raw.parquet", [product_row("1", "생수 2L")])
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))
    catalog.derive_product_ingredients(rows, {"물": 1})

    assert rows.product_ingredients == []


def test_existing_ingredients_are_not_overwritten(tmp_path: Path) -> None:
    """raw 로 들어온 구성 재료가 있으면 유추하지 않습니다."""
    write_parquet(
        tmp_path / "product_raw.parquet",
        [product_row("1", "양념 돼지불고기", ingredients=json.dumps([{"name": "돼지고기"}], ensure_ascii=False))],
    )
    rows = catalog.build_catalog_rows(discover_datasets(tmp_path))
    derived = catalog.derive_product_ingredients(rows, {"불고기": 99})

    assert derived == 0
    assert [row[2] for row in rows.product_ingredients] == ["돼지고기"]
