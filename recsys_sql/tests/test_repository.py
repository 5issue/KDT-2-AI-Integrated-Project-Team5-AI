"""서빙이 쓰는 repository 진입점 검증.

여기서 깨지면 API 가 통째로 멈춥니다. DB 없이 도는 테스트로 둡니다.
"""

from __future__ import annotations

import pytest

from recsys_sql import CatalogError, bind_asyncpg, find_query, prepare


def test_named_params_become_positional() -> None:
    """카탈로그는 `:name`, asyncpg 는 `$1` 입니다. 두 벌을 손으로 관리하지 않습니다."""
    sql, args = prepare("product_detail", {"product_id": 101})

    assert ":product_id" not in sql
    assert "$1" in sql
    assert args == (101,)


def test_repeated_param_reuses_one_number() -> None:
    """같은 이름이 여러 번 나와도 인자는 하나입니다.

    `product_recipes` 는 `:product_id` 를 두 번 씁니다(구성 재료 조회와 우선순위 조인).
    번호를 새로 매기면 호출부가 같은 값을 두 번 넘겨야 하고, 그러다 순서가 어긋납니다.
    """
    query = find_query("product_recipes")
    body = "\n".join(line for line in query.sql.splitlines() if not line.lstrip().startswith("--"))
    assert body.count(":product_id") == 2

    sql, args = bind_asyncpg(query, {"product_id": 7, "max_results": 10, "skip": 0})
    converted = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))

    assert args == (7, 10, 0)
    assert converted.count("$1") == 2


def test_casts_are_left_alone() -> None:
    """`::numeric` 같은 캐스팅을 바인딩으로 잡으면 SQL 이 깨집니다."""
    sql, _ = prepare("my_recipe_candidates", {"user_id": 1, "min_match_rate": 0.5, "max_results": 10})

    assert "::numeric" in sql


def test_header_params_line_is_not_converted() -> None:
    """헤더의 `-- params: user_id:int` 가 변환에 섞이면 안 됩니다."""
    sql, _ = prepare("my_fridge_items", {"user_id": 1})

    assert "-- params: user_id:int" in sql


def test_missing_param_fails_before_the_database() -> None:
    """DB 까지 가서 터지는 것보다 여기서 걸리는 편이 낫습니다."""
    with pytest.raises(CatalogError, match="파라미터가 빠졌습니다"):
        prepare("product_recipes", {"product_id": 1})


def test_wrong_type_fails_before_the_database() -> None:
    """타입이 어긋나면 asyncpg 가 내는 메시지보다 이쪽이 읽기 쉽습니다."""
    with pytest.raises(CatalogError, match="int 을 기대했지만"):
        prepare("product_detail", {"product_id": "101"})


def test_unknown_query_names_the_alternatives() -> None:
    """오타로 API 가 죽을 때 뭐가 있는지는 알려 줘야 합니다."""
    with pytest.raises(CatalogError, match="카탈로그에 없는 쿼리입니다"):
        find_query("no_such_query")


def test_every_catalog_query_can_be_bound() -> None:
    """카탈로그 전체가 asyncpg 형식으로 변환되는지 한 번에 봅니다.

    주석에 `:표기` 를 적어 두면 실행 시점에야 터집니다. 여기서 미리 잡습니다.
    """
    from recsys_sql.catalog import load_catalog
    from recsys_sql.config import get_settings

    samples: dict[str, object] = {"int": 1, "float": 0.5, "str": "x", "bool": True}
    for query in load_catalog(get_settings().queries_dir):
        params = {name: samples[type_name] for name, type_name in query.params.items()}
        sql, args = bind_asyncpg(query, params)
        assert len(args) == len(query.params), query.name
        assert not [
            line
            for line in sql.splitlines()
            if not line.lstrip().startswith("--") and ":" in line and "::" not in line and "'" not in line
        ], query.name
