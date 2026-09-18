"""CLI 특성화 테스트. **리팩토링 전 동작을 못으로 박아 두는 것이 목적입니다.**

`cli.py` 는 570줄인데 직접 테스트가 없었습니다. 적재 오케스트레이션을 도메인 모듈로
내리기 전에, 밖에서 보이는 동작(서브커맨드 목록, 기본값, 종료코드, 실패 메시지)을
먼저 고정합니다.

**그래서 내부 함수를 부르지 않고 `main(argv)` 를 그대로 돌립니다.** 구현이 어느 모듈로
옮겨가든 이 파일은 그대로여야 합니다. 옮긴 뒤 여기가 깨지면 그건 동작이 바뀐 것입니다.

DB 는 건드리지 않습니다. 각 명령이 DB 에 닿기 **전에** 빠져나오는 경로만 씁니다
(산출물 없음 / 데이터셋 없음). DB 가 필요한 경로는 `@pytest.mark.db` 가 붙은
다른 파일들이 이미 덮고 있습니다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from tests_helpers import write_jsonl, write_parquet

from data_pipeline import cli
from data_pipeline.config import Settings

# 서브커맨드 목록. 늘리거나 줄이는 것은 의도적인 변경이어야 합니다.
EXPECTED_COMMANDS = {
    "check-db",
    "collect",
    "embed",
    "extract",
    "inspect",
    "load",
    "load-catalog",
    "load-recipes",
    "profile",
    "resolve",
    "seed-demo",
    "status",
    "submit",
    "sync-master",
}


@pytest.fixture
def cli_settings(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """`cli` 가 보는 설정을 tmp_path 로 돌립니다.

    `cli` 는 명령마다 `get_settings()` 를 부르고 그 값을 아래로 넘깁니다. 이 구조는
    오케스트레이션을 옮겨도 그대로라서, 여기 한 곳만 바꿔 끼우면 됩니다.
    """
    monkeypatch.setattr(cli, "get_settings", lambda: tmp_settings)
    return tmp_settings


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    """서브커맨드 이름 -> 파서."""
    actions = [
        action
        for action in cli.build_parser()._actions  # noqa: SLF001 - argparse 가 공개 API 를 주지 않습니다
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    ]
    return dict(actions[0].choices)


# --- 파서 계약 ---------------------------------------------------------------


def test_subcommand_list_is_stable() -> None:
    assert set(_subparsers()) == EXPECTED_COMMANDS


def test_every_subcommand_binds_a_function() -> None:
    """`main` 이 `args.func` 를 부릅니다. 하나라도 비면 그 명령은 즉시 터집니다."""
    parser = cli.build_parser()
    for name in EXPECTED_COMMANDS:
        args = parser.parse_args([name, *(["--job", "j"] if name in _JOB_REQUIRED else []), *_EXTRA_ARGS.get(name, [])])
        assert callable(args.func), name


_JOB_REQUIRED = {"profile", "extract", "resolve", "submit", "collect"}
_EXTRA_ARGS = {"submit": ["--stage", "profile"], "collect": ["--stage", "profile"]}


@pytest.mark.parametrize(
    ("argv", "attribute", "expected"),
    [
        (["inspect"], "limit", 2),
        (["submit", "--stage", "profile", "--job", "j"], "parts", 1),
        (["collect", "--stage", "profile", "--job", "j"], "poll", 60),
        (["collect", "--stage", "profile", "--job", "j"], "wait", False),
        (["seed-demo"], "users", 20),
        (["seed-demo"], "reset_stock", False),
        (["embed"], "target", "all"),
        (["embed"], "refresh", False),
        (["load-catalog"], "apply", False),
        (["load"], "truncate_staging", False),
    ],
)
def test_defaults(argv: list[str], attribute: str, expected: object) -> None:
    """기본값이 바뀌면 같은 명령이 다른 일을 합니다. 특히 `--apply` 는 기본이 거짓이어야 합니다."""
    assert getattr(cli.build_parser().parse_args(argv), attribute) == expected


@pytest.mark.parametrize("name", sorted(_JOB_REQUIRED))
def test_job_is_required(name: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([name, *_EXTRA_ARGS.get(name, [])])


def test_stage_choices_are_the_three_pipeline_stages() -> None:
    for stage in ("profile", "extract", "resolve"):
        assert cli.build_parser().parse_args(["submit", "--stage", stage, "--job", "j"]).stage == stage
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["submit", "--stage", "load", "--job", "j"])


# --- raw 경로 해석 -----------------------------------------------------------


def test_raw_path_prefers_the_flag(tmp_settings: Settings, tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(["inspect", "--raw", str(tmp_path / "elsewhere")])
    assert cli.raw_path(args, tmp_settings) == tmp_path / "elsewhere"


def test_raw_path_falls_back_to_settings(tmp_settings: Settings) -> None:
    args = cli.build_parser().parse_args(["inspect"])
    assert cli.raw_path(args, tmp_settings) == tmp_settings.raw_dir


# --- DB 없이 도는 명령 -------------------------------------------------------


def test_status_reports_the_raw_directory(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status"]) == 0
    assert str(cli_settings.raw_dir) in capsys.readouterr().out


def test_inspect_reads_the_given_raw_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_jsonl(tmp_path / "raw" / "sample.jsonl", [{"name": "두부", "price": 3000}])
    assert cli.main(["inspect", "--raw", str(tmp_path / "raw")]) == 0
    assert "sample" in capsys.readouterr().out


# --- 산출물이 없을 때의 종료코드와 메시지 ------------------------------------
#
# 아래 넷이 이번 리팩토링에서 실제로 옮겨지는 함수들의 이른 반환 경로입니다.
# DB 에 닿기 전에 빠져나오므로 연결 없이 돌고, 옮긴 뒤에도 똑같아야 합니다.


def test_resolve_without_stage2_records_fails(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["resolve", "--job", "r1"]) == 1
    assert "2단계 산출물에 재료명이 없습니다." in capsys.readouterr().err


def test_load_without_match_artifact_fails(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """3단계 산출물이 없으면 레코드를 세기도 전에 멈춥니다.

    `ingredient_matches.json` 은 gitignore 대상이라 새 체크아웃에서 가장 먼저 부딪히는
    실패입니다. 메시지가 다음에 할 일("3단계를 먼저 끝내세요")을 알려 주는 것이 중요합니다.
    """
    assert cli.main(["load"]) == 1
    assert "매칭 결과가 없습니다" in capsys.readouterr().err


def test_sync_master_without_apply_only_counts(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """대표식품 컬럼이 없어도 0행이 되지 않습니다.

    `CURATED_BASICS` 10종이 raw 와 무관하게 항상 붙기 때문입니다. 그래서 "데이터셋을
    찾지 못했습니다" 분기로는 빠지지 않고, `--apply` 가 없어 집계만 하고 끝납니다.
    **DB 에 닿지 않는 것이 이 테스트의 요점입니다.**
    """
    write_parquet(cli_settings.raw_dir / "unrelated.parquet", [{"name": "두부", "price": "3000"}])
    assert cli.main(["sync-master"]) == 0
    out = capsys.readouterr().out
    assert "마스터 후보" in out
    assert "--apply 를 붙이면 실제로 반영합니다." in out


def test_load_catalog_without_catalog_columns_fails(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    write_parquet(cli_settings.raw_dir / "unrelated.parquet", [{"foo": "1", "bar": "2"}])
    assert cli.main(["load-catalog"]) == 1
    assert "카테고리/상품 데이터셋을 찾지 못했습니다." in capsys.readouterr().err


def test_load_recipes_without_cookrcp_columns_fails(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    write_parquet(cli_settings.raw_dir / "unrelated.parquet", [{"foo": "1", "bar": "2"}])
    assert cli.main(["load-recipes"]) == 1
    assert "COOKRCP01 데이터셋을 찾지 못했습니다." in capsys.readouterr().err


def test_missing_raw_directory_becomes_exit_code_1(cli_settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """raw 폴더 자체가 없으면 `discover_datasets` 가 FileNotFoundError 를 냅니다.

    `main` 이 그것을 잡아 종료코드 1 로 바꿉니다. 스택트레이스를 그대로 내보내지 않습니다.
    """
    for entry in cli_settings.raw_dir.iterdir():
        entry.unlink()
    cli_settings.raw_dir.rmdir()
    assert cli.main(["load-catalog"]) == 1
    assert "실패:" in capsys.readouterr().err


# --- main 의 예외 계약 -------------------------------------------------------


def test_main_returns_the_command_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "command_status", lambda _: 7)
    assert cli.main(["status"]) == 7


@pytest.mark.parametrize("error", [RuntimeError("터짐"), FileNotFoundError("없음"), ValueError("이상함")])
def test_main_maps_known_errors_to_exit_code_1(
    error: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """사용자에게는 한 줄 메시지만 보여 주고 1 을 냅니다."""

    def boom(_: argparse.Namespace) -> int:
        raise error

    monkeypatch.setattr(cli, "command_status", boom)
    assert cli.main(["status"]) == 1
    assert "실패:" in capsys.readouterr().err
