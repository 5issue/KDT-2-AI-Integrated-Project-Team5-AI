"""SQL 카탈로그 정적 검증. DB 없이 항상 돕니다."""

from __future__ import annotations

from pathlib import Path

import pytest

from recsys_sql.catalog import CatalogError, load_catalog, load_query
from recsys_sql.config import PACKAGE_DIR

QUERIES_DIR = PACKAGE_DIR / "queries"


def test_every_sql_file_in_catalog_is_valid() -> None:
    """queries/ 아래 모든 .sql 이 헤더 규약과 파라미터 규약을 지키는지 확인합니다."""
    queries = load_catalog(QUERIES_DIR)
    assert queries, "카탈로그가 비어 있습니다."
    for query in queries:
        assert query.params.keys() == query.bind_names, query.name


def test_template_queries_are_registered() -> None:
    """예시 3종이 그대로 남아 있는지 확인합니다."""
    names = {query.name for query in load_catalog(QUERIES_DIR / "_template")}
    assert names == {"fridge_recipe_match", "missing_ingredient_products", "reorder_candidates"}


def test_duplicate_name_is_rejected(tmp_path: Path) -> None:
    """서로 다른 폴더에 같은 이름이 있으면 실패시킵니다."""
    body = "-- name: {stem}\n-- owner: tester\n-- description: 중복 확인\nSELECT 1 AS one;\n"
    for folder in ("a", "b"):
        directory = tmp_path / folder
        directory.mkdir()
        (directory / "dup.sql").write_text(body.format(stem="dup"), encoding="utf-8")

    with pytest.raises(CatalogError, match="중복"):
        load_catalog(tmp_path)


def test_undeclared_bind_param_is_rejected(tmp_path: Path) -> None:
    """본문에 쓴 바인딩을 params 에 안 적으면 실패합니다."""
    path = tmp_path / "leaky.sql"
    path.write_text(
        "-- name: leaky\n-- owner: tester\n-- description: 선언 누락\n"
        "SELECT * FROM recipe WHERE recipe_id = :recipe_id;\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="params 에 없는"):
        load_query(path)


def test_write_statement_is_rejected(tmp_path: Path) -> None:
    """카탈로그 쿼리에 쓰기 구문이 섞이면 실패합니다."""
    path = tmp_path / "writer.sql"
    path.write_text(
        "-- name: writer\n-- owner: tester\n-- description: 쓰기 시도\nDELETE FROM recipe WHERE recipe_id = 1;\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="읽기 전용"):
        load_query(path)


def test_cast_operator_is_not_a_bind_param(tmp_path: Path) -> None:
    """`::numeric` 같은 캐스팅을 바인딩으로 오인하지 않아야 합니다."""
    path = tmp_path / "casting.sql"
    path.write_text(
        "-- name: casting\n-- owner: tester\n-- description: 캐스팅 확인\n-- params: limit_count:int\n"
        "SELECT (1::numeric / 3)::text AS ratio FROM recipe LIMIT :limit_count;\n",
        encoding="utf-8",
    )
    assert load_query(path).bind_names == {"limit_count"}
