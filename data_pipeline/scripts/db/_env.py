"""DB 스크립트가 함께 쓰는 접속 설정 읽기와 Production 확인 문자열 검사입니다.

승격·적재 스크립트마다 같은 함수가 복사돼 있었습니다. 한 곳에 둡니다.
접속 문자열 값은 어디서도 출력하지 않습니다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

Target = Literal["local", "production"]


def load_env(path: Path) -> dict[str, str]:
    """비밀 값을 출력하지 않고 단순 env 파일을 읽습니다."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_url(values: dict[str, str], key: str) -> str:
    """환경 변수 또는 env 파일에서 접속 문자열을 읽습니다. 값 자체는 출력하지 않습니다."""
    value = os.environ.get(key) or values.get(key)
    if not value:
        raise ValueError(f"{key}가 설정되지 않았습니다. 접속 문자열 값은 출력하지 않습니다.")
    return value


def validate_confirmation(target: Target, apply: bool, confirmation: str | None, *, token: str) -> None:
    """Production 에 실제로 쓰기 전 확인 문자열을 요구합니다. 스크립트마다 문자열이 다릅니다."""
    if target == "production" and apply and confirmation != token:
        raise ValueError(f"Production 적용에는 --confirm-production {token} 이 필요합니다.")
