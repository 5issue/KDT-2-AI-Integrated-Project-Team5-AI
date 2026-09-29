"""바뀐 파일로 CI 에서 돌릴 DB 테스트 폴더를 고릅니다. 표준 라이브러리만 씁니다.

DB 테스트를 전부 돌리면 GitHub 러너(미국)와 Neon(ap-southeast-1) 사이 왕복 지연 때문에 20분을 넘깁니다.
단위 테스트(DB 없음)는 CI 가 항상 전부 돌리고, DB 테스트는 이 스크립트가 고른 폴더만 돌립니다.

    python3 .github/scripts/select_db_tests.py --base origin/main      # PR: 기준 브랜치와의 merge-base 부터
    python3 .github/scripts/select_db_tests.py --base <before-sha>    # push: 직전 커밋부터
    python3 .github/scripts/select_db_tests.py --all                  # 수동 실행: 전부
    python3 .github/scripts/select_db_tests.py --files a.py b.sql     # 규칙 확인용

출력은 공백으로 구분한 테스트 폴더입니다. 돌릴 것이 없으면 빈 줄입니다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

ALL_DIRS = (
    "data_pipeline/tests",
    "data_pipeline/scripts",
    "rag_lab/tests",
    "recsys_sql/tests",
    "serving/tests",
)

# 이 파일이 바뀌면 전부 돌립니다. 스키마(마이그레이션), 테스트 공통 설정, 의존성, CI 자체입니다.
RUN_ALL_FILES = frozenset({"conftest.py", "pyproject.toml", "uv.lock", ".github/workflows/ci.yaml"})
RUN_ALL_PREFIXES = ("database/", ".github/scripts/")

# 폴더가 바뀌면 돌릴 테스트. 의존하는 쪽까지 넣습니다.
# serving 은 recsys_sql 카탈로그 SQL 과 rag_lab.reason_service 를 import 하므로 둘이 바뀌면 serving 도 돌립니다.
DEPENDENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_pipeline/", ("data_pipeline/tests", "data_pipeline/scripts")),
    ("config/", ("data_pipeline/tests", "data_pipeline/scripts")),
    ("recsys_sql/", ("recsys_sql/tests", "serving/tests")),
    ("rag_lab/", ("rag_lab/tests", "serving/tests")),
    ("serving/", ("serving/tests",)),
)


def select(paths: list[str]) -> list[str]:
    """바뀐 경로 목록에서 돌릴 DB 테스트 폴더를 ALL_DIRS 순서로 돌려줍니다."""
    chosen: set[str] = set()
    for path in paths:
        # 문서는 어느 폴더에 있든 DB 테스트와 무관합니다(README, 설계 문서).
        if path.endswith(".md"):
            continue
        if path in RUN_ALL_FILES or path.startswith(RUN_ALL_PREFIXES):
            return list(ALL_DIRS)
        for prefix, dirs in DEPENDENTS:
            if path.startswith(prefix):
                chosen.update(dirs)
    return [directory for directory in ALL_DIRS if directory in chosen]


def changed_files(base: str) -> list[str] | None:
    """``base`` 부터 HEAD 까지 바뀐 파일. 기준을 못 찾으면 None (그러면 전부 돌립니다)."""
    if not base or set(base) == {"0"}:
        return None
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"기준 {base} 와 비교하지 못해 전부 돌립니다: {result.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base", help="비교 기준 (origin/main 또는 커밋 sha)")
    group.add_argument("--all", action="store_true", help="전부 돌립니다")
    group.add_argument("--files", nargs="*", help="바뀐 파일을 직접 줍니다 (규칙 확인용)")
    args = parser.parse_args(argv)

    if args.all:
        paths = None
    elif args.files is not None:
        paths = args.files
    else:
        paths = changed_files(args.base)

    dirs = list(ALL_DIRS) if paths is None else select(paths)
    print(" ".join(dirs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
