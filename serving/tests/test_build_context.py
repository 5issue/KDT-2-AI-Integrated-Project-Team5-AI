"""루트 `.dockerignore` 가 실제로 무엇을 보내는지 검증.

`.env` 가 이미지에 들어가면 레이어에 남아 `docker history` 로 읽힙니다. 한 번 올라간
이미지는 회수하기 어렵고, 빌드 로그만 봐서는 들어갔는지 알 수 없습니다. 그래서 여기서
막습니다.

빌드 컨텍스트가 **레포 루트**라는 점이 이 검사를 필요하게 만듭니다. `serving` 이
`recsys_sql` 을 워크스페이스 의존성으로 받기 때문에 `serving/` 만 보낼 수 없고,
그래서 루트의 모든 것이 후보가 됩니다.

Docker(moby/patternmatcher) 규칙을 그대로 옮겨 적용합니다.
- 경로 전체에 매칭하고, `*` 는 `/` 를 넘지 않습니다. 깊이를 넘기려면 `**`.
- 나중에 나온 패턴이 이기고, `!` 는 제외를 되돌립니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def _to_regex(pattern: str) -> re.Pattern[str]:
    """`.dockerignore` 패턴 하나를 정규식으로."""
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            out += ".*"
            index += 2
        elif pattern[index] == "*":
            out += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            out += "[^/]"
            index += 1
        else:
            out += re.escape(pattern[index])
            index += 1
    return re.compile(f"^{out}(?:/.*)?$")


def _rules() -> list[tuple[bool, re.Pattern[str]]]:
    """(되돌리기 여부, 정규식) 목록. 파일에 적힌 순서를 지킵니다."""
    rules = []
    for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rules.append((line.startswith("!"), _to_regex(line.lstrip("!").rstrip("/"))))
    return rules


def is_excluded(relative_path: str) -> bool:
    """이 경로가 빌드 컨텍스트에서 빠지는지."""
    verdict = False
    for negate, matcher in _rules():
        if matcher.match(relative_path):
            verdict = not negate
    return verdict


@pytest.mark.parametrize(
    "path",
    [
        "serving/.env",
        "recsys_sql/.env",
        "rag_lab/.env",
        "data_pipeline/.env",
        "database/.env",
        "serving/.env.local",
        "deploy/prod.tfvars",
        "certs/server.pem",
    ],
)
def test_secrets_never_reach_the_image(path: str) -> None:
    """이미지 레이어에 남으면 `docker history` 로 읽힙니다."""
    assert is_excluded(path), f"{path} 가 빌드 컨텍스트에 들어갑니다"


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "uv.lock",
        "serving/pyproject.toml",
        "serving/src/serving/app.py",
        "recsys_sql/pyproject.toml",
        "recsys_sql/src/recsys_sql/repository.py",
        # serving 은 .sql 을 갖지 않고 이 카탈로그를 씁니다. 빠지면 첫 요청에서 죽습니다.
        "recsys_sql/queries/openLeeWorld/product_detail.sql",
        "recsys_sql/queries/_template/fridge_recipe_match.sql",
    ],
)
def test_build_inputs_survive(path: str) -> None:
    """빌드가 쓰는 파일이 빠지면 이미지가 안 만들어지거나 런타임에 죽습니다."""
    assert not is_excluded(path), f"{path} 가 제외되어 빌드가 깨집니다"


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        ".venv/pyvenv.cfg",
        "data_pipeline/data/raw/recipes.parquet",
        "ai_context/api_spec.md",
        "notebooks/01_explore.ipynb",
    ],
)
def test_heavy_and_private_paths_are_dropped(path: str) -> None:
    """컨텍스트가 커지면 빌드가 느려지고 캐시가 자주 깨집니다."""
    assert is_excluded(path)


def test_context_stays_small() -> None:
    """실제 레포를 훑어 컨텍스트 크기를 잽니다.

    무거운 것이 새로 들어오면 여기서 먼저 걸립니다.
    """
    total = 0
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = str(path.relative_to(REPO_ROOT))
        except ValueError:  # pragma: no cover
            continue
        if not is_excluded(relative):
            total += path.stat().st_size

    megabytes = total / 1024 / 1024
    assert megabytes < 20, f"빌드 컨텍스트가 {megabytes:.1f} MB 입니다. .dockerignore 를 확인하세요."
