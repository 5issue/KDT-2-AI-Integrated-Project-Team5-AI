"""재채점 경로가 생성 클라이언트를 만들지 않는지. API 도 DB 도 쓰지 않습니다.

`--rescore` 는 지난 문구를 다시 채점만 합니다. 그런데 생성 클라이언트를 먼저 만들면
`LLM_API_KEY` 를 요구해서, **판정용 키만 가진 사람이 재채점을 못 돌립니다.**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rag_lab import cli
from rag_lab.recommendation import RUBRIC_ITEMS

pytestmark = pytest.mark.asyncio


class StubJudge:
    """`LlmJudgeClient` 자리에 끼우는 가짜. 항상 만점을 줍니다."""

    model = "stub-judge"

    class provider:  # noqa: N801 - 실제 클라이언트의 속성 모양을 흉내 냅니다
        name = "stub"

    async def complete(self, system: str, user: str) -> str:
        scores = {key: 9 for key, _, _ in RUBRIC_ITEMS}
        return json.dumps({**scores, "comment": "좋음"}, ensure_ascii=False)


def _cases_file(tmp_path: Path) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {"case_id": "a", "recipe": "두부조림", "match_rate": 0.5, "have": ["두부"], "missing": ["참기름"]},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _prior_results(tmp_path: Path) -> Path:
    path = tmp_path / "prior.jsonl"
    meta = json.dumps({"name": "prior", "params": {}}, ensure_ascii=False)
    row = json.dumps({"case_id": "a", "reason": "두부는 있으니 참기름만 더하면 됩니다."}, ensure_ascii=False)
    path.write_text(f"{meta}\n{row}\n", encoding="utf-8")
    return path


async def test_rescore_never_builds_the_generation_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """생성 클라이언트를 만들면 `LLM_API_KEY` 가 필요해집니다. 이 경로는 생성이 없습니다."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("재채점에서 생성 클라이언트를 만들면 안 됩니다")

    monkeypatch.setattr(cli, "LlmChatClient", refuse)
    monkeypatch.setattr(cli, "LlmJudgeClient", lambda *_args, **_kwargs: StubJudge())

    exit_code = await cli.run_reason_command(
        "rescored",
        _cases_file(tmp_path),
        tmp_path / "out",
        judge=True,
        rescore=_prior_results(tmp_path),
    )
    assert exit_code == 0


async def test_rescore_requires_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """채점만 하는 명령이라 판정자 없이 부르면 아무 일도 안 합니다. 조용히 넘기지 않습니다."""
    monkeypatch.setattr(cli, "LlmChatClient", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="--judge"):
        await cli.run_reason_command(
            "x", _cases_file(tmp_path), tmp_path / "out", judge=False, rescore=_prior_results(tmp_path)
        )
