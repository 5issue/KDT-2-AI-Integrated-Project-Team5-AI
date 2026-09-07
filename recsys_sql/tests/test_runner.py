"""파라미터 검증 테스트. DB 없이 돕니다."""

from __future__ import annotations

import pytest

from recsys_sql.catalog import CatalogError, load_catalog
from recsys_sql.config import PACKAGE_DIR
from recsys_sql.runner import validate_params

TEMPLATE_DIR = PACKAGE_DIR / "queries" / "_template"


@pytest.fixture(scope="module")
def fridge_query():  # type: ignore[no-untyped-def]
    """예시 쿼리 하나."""
    return next(q for q in load_catalog(TEMPLATE_DIR) if q.name == "fridge_recipe_match")


def test_accepts_declared_params(fridge_query) -> None:  # type: ignore[no-untyped-def]
    """선언된 파라미터가 다 오면 통과합니다."""
    params = {"user_id": 1, "min_coverage": 0.5, "max_results": 10}
    assert validate_params(fridge_query, params) == params


def test_missing_param_is_rejected(fridge_query) -> None:  # type: ignore[no-untyped-def]
    """빠진 파라미터는 실행 전에 걸립니다."""
    with pytest.raises(CatalogError, match="빠졌습니다"):
        validate_params(fridge_query, {"user_id": 1, "min_coverage": 0.5})


def test_extra_param_is_rejected(fridge_query) -> None:  # type: ignore[no-untyped-def]
    """오타로 넘긴 파라미터가 조용히 무시되지 않습니다."""
    with pytest.raises(CatalogError, match="선언되지 않은"):
        validate_params(fridge_query, {"user_id": 1, "min_coverage": 0.5, "max_results": 10, "limit": 3})


def test_bool_is_not_accepted_as_int(fridge_query) -> None:  # type: ignore[no-untyped-def]
    """파이썬에서 bool 은 int 의 서브클래스라 따로 막습니다."""
    with pytest.raises(CatalogError, match="bool"):
        validate_params(fridge_query, {"user_id": True, "min_coverage": 0.5, "max_results": 10})


def test_int_is_accepted_where_float_declared(fridge_query) -> None:  # type: ignore[no-untyped-def]
    """float 자리에 int 는 허용합니다."""
    validate_params(fridge_query, {"user_id": 1, "min_coverage": 1, "max_results": 10})
