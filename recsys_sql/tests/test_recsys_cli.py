"""CLI 파라미터 해석과 파서 계약. **DB 를 쓰지 않습니다.**

`--param user_id=1` 을 카탈로그가 선언한 타입으로 바꾸는 곳인데 검증이 없었습니다.
여기가 틀리면 `--param max_results=5` 가 문자열 `"5"` 로 들어가 DB 에서 타입 에러가
나거나, 더 나쁘게는 조용히 다른 결과가 나옵니다.

`run` / `explain` 의 실제 실행은 DB 가 필요해 다루지 않습니다. 그쪽은
`test_recommendation_queries.py` 등이 쿼리 단위로 덮고 있습니다.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from recsys_sql import cli
from recsys_sql.catalog import CatalogError

EXPECTED_COMMANDS = {"check-db", "explain", "list", "run"}


# --- parse_param: 선언 타입대로 바꾸기 ---------------------------------------


@pytest.mark.parametrize(
    ("raw", "declared", "expected"),
    [
        ("42", "int", 42),
        ("-7", "int", -7),
        ("0.5", "float", 0.5),
        ("3", "float", 3.0),
        ("김치", "str", "김치"),
        ("1, 2,3", "list[int]", [1, 2, 3]),
        ("1,,2", "list[int]", [1, 2]),
        ("가, 나", "list[str]", ["가", "나"]),
        ("", "list[str]", []),
    ],
)
def test_parse_param_converts_by_declared_type(raw: str, declared: str, expected: Any) -> None:
    assert cli.parse_param(raw, declared) == expected


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Y", " true "])
def test_parse_param_reads_these_as_true(raw: str) -> None:
    assert cli.parse_param(raw, "bool") is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "", "참"])
def test_parse_param_reads_everything_else_as_false(raw: str) -> None:
    assert cli.parse_param(raw, "bool") is False


def test_parse_param_leaves_unknown_types_alone() -> None:
    """모르는 타입은 문자열 그대로 넘깁니다. 카탈로그가 타입을 늘려도 여기서 안 터집니다."""
    assert cli.parse_param("13", "jsonb") == "13"


def test_parse_param_rejects_a_non_number_for_int() -> None:
    """`main` 이 ValueError 를 잡아 종료코드 1 로 바꿉니다."""
    with pytest.raises(ValueError):
        cli.parse_param("다섯", "int")


# --- collect_params: 카탈로그 선언과 맞추기 ----------------------------------


def test_collect_params_uses_the_catalog_declaration() -> None:
    params = cli.collect_params("my_recipe_candidates", ["user_id=5", "min_match_rate=0.3", "max_results=10"])
    assert params == {"user_id": 5, "min_match_rate": 0.3, "max_results": 10}
    assert isinstance(params["min_match_rate"], float)


def test_collect_params_trims_the_name() -> None:
    assert cli.collect_params("my_fridge_items", [" user_id = 5"]) == {"user_id": 5}


def test_collect_params_keeps_equals_signs_inside_the_value() -> None:
    """`partition` 은 첫 `=` 에서만 끊습니다. 값에 `=` 가 있어도 살아남아야 합니다."""
    params = cli.collect_params("bubble_products", ["keyword_id=a=b", "max_results=5", "skip=0"])
    assert params["keyword_id"] == "a=b"


def test_collect_params_without_equals_is_rejected() -> None:
    with pytest.raises(CatalogError, match="name=value"):
        cli.collect_params("my_fridge_items", ["user_id"])


def test_collect_params_rejects_a_name_the_query_does_not_declare() -> None:
    """오타를 조용히 넘기면 그 값이 빠진 채로 실행됩니다."""
    with pytest.raises(CatalogError, match="없는 파라미터"):
        cli.collect_params("my_fridge_items", ["users_id=5"])


def test_collect_params_allows_a_subset() -> None:
    """빠진 파라미터는 여기서 막지 않습니다. `validate_params` 가 실행 직전에 잡습니다."""
    assert cli.collect_params("my_recipe_candidates", ["user_id=5"]) == {"user_id": 5}


def test_collect_params_on_an_unknown_query_raises_key_error() -> None:
    with pytest.raises(KeyError):
        cli.collect_params("no_such_query", [])


# --- 파서 계약 ---------------------------------------------------------------


def test_subcommand_list_is_stable() -> None:
    actions = [a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction)]  # noqa: SLF001
    assert set(actions[0].choices) == EXPECTED_COMMANDS


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_every_subcommand_binds_a_function(name: str) -> None:
    extra = ["--query", "my_fridge_items"] if name in {"run", "explain"} else []
    assert callable(cli.build_parser().parse_args([name, *extra]).func)


@pytest.mark.parametrize("name", ["run", "explain"])
def test_query_is_required(name: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([name])


@pytest.mark.parametrize("name", ["run", "explain"])
def test_param_accumulates_and_defaults_to_empty(name: str) -> None:
    """`action="append"` 라 여러 번 쓸 수 있어야 합니다. 하나만 남으면 나머지가 사라집니다."""
    assert cli.build_parser().parse_args([name, "--query", "q"]).param == []
    args = cli.build_parser().parse_args([name, "--query", "q", "--param", "a=1", "--param", "b=2"])
    assert args.param == ["a=1", "b=2"]


def test_check_db_direct_defaults_to_false() -> None:
    assert cli.build_parser().parse_args(["check-db"]).direct is False


# --- list 와 main 의 계약 ----------------------------------------------------


def test_list_prints_every_query_with_its_params(capsys: pytest.CaptureFixture[str]) -> None:
    """`QUERY_OWNER` 와 무관하게 카탈로그 전체를 봅니다."""
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "my_recipe_candidates" in out
    assert "user_id:int" in out


def test_list_on_an_empty_catalog_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_catalog", lambda *_args, **_kwargs: [])
    assert cli.main(["list"]) == 0
    assert "등록된 쿼리가 없습니다." in capsys.readouterr().out


def test_main_returns_the_command_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "command_list", lambda _: 3)
    assert cli.main(["list"]) == 3


@pytest.mark.parametrize("error", [RuntimeError("터짐"), FileNotFoundError("없음"), CatalogError("계약 위반")])
def test_main_maps_known_errors_to_exit_code_1(
    error: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """스택트레이스 대신 한 줄만 보여 줍니다. CatalogError 는 ValueError 라 여기 걸립니다."""

    def boom(_: argparse.Namespace) -> int:
        raise error

    monkeypatch.setattr(cli, "command_list", boom)
    assert cli.main(["list"]) == 1
    assert "실패:" in capsys.readouterr().err
