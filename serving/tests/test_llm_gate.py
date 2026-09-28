"""과금이 있는 llm 테스트가 명시 플래그(`--run-llm`) 없이는 돌지 않는지 봅니다. DB·LLM 을 부르지 않습니다.

PR #42 리뷰: 예전 게이트는 `-m` 표현식에 `llm` 이라는 글자가 있는지로 판단했습니다. 그래서 오히려 llm 을
빼려는 `-m "db or not llm"` 이나, `llm_mock` 같은 다른 마커 이름이 든 표현식에서 게이트가 열렸고, llm 테스트는
모듈의 `db` 마커로 선택되어 실제 OpenRouter 를 불렀습니다.

"열리는" 경우(`--run-llm`)는 여기서 돌리지 않습니다. 로컬 `.env` 에 키와 DB 가 있으면 실제로 과금되기 때문입니다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_TEST = "serving/tests/test_api_integration.py::test_my_recipes_with_real_openrouter"


@pytest.mark.parametrize(
    "markexpr",
    [None, "llm", "db or not llm", "db or llm_mock"],
    ids=["no-m", "m-llm", "db-or-not-llm", "db-or-llm_mock"],
)
def test_llm_test_is_skipped_without_run_llm_flag(markexpr: str | None) -> None:
    """어떤 `-m` 표현식이든 `--run-llm` 이 없으면 llm 테스트는 과금 사유로 skip 됩니다."""
    command = [sys.executable, "-m", "pytest", LLM_TEST, "-q", "-rs", "-p", "no:cacheprovider"]
    if markexpr is not None:
        command += ["-m", markexpr]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8")

    assert "1 skipped" in result.stdout, result.stdout[-2000:]
    assert "--run-llm" in result.stdout, "skip 사유가 llm 게이트가 아닙니다: " + result.stdout[-2000:]
