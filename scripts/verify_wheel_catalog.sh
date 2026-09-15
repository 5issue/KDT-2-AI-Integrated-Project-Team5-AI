#!/usr/bin/env bash
#
# recsys_sql 휠에 SQL 카탈로그가 실제로 담겼는지 확인합니다.
#
# `queries/` 가 패키지 밖에 있어서, hatch 의 force-include 설정이 빠지면 휠에 .sql 이
# 한 장도 안 들어갑니다. 개발 중에는 `uv sync` 가 editable 로 설치해 `__file__` 이
# 레포 안을 가리키므로 아무 문제가 없어 보이고, 이미지를 만드는 순간
# (`uv sync --no-editable`) 첫 요청에서 FileNotFoundError 로 죽습니다.
#
# 쿼리를 추가하는 것과 휠에 담기는 것은 별개라, 사람이 기억할 일로 두면 언젠가 빠집니다.
#
#   ./scripts/verify_wheel_catalog.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

expected="$(find "$repo_root/recsys_sql/queries" -name '*.sql' | wc -l | tr -d ' ')"
echo "레포의 .sql: ${expected}장"

uv build --package recsys-sql --wheel --out-dir "$work/dist" >/dev/null
wheel="$(find "$work/dist" -name '*.whl' | head -1)"

packed="$(unzip -Z1 "$wheel" | grep -c '\.sql$' || true)"
echo "휠 안의 .sql: ${packed}장"

if [ "$packed" != "$expected" ]; then
    echo "실패: 휠에 담긴 SQL 수가 레포와 다릅니다." >&2
    echo "      recsys_sql/pyproject.toml 의 [tool.hatch.build.targets.wheel.force-include] 를 확인하세요." >&2
    exit 1
fi

# 휠만 깐 깨끗한 환경에서 카탈로그를 실제로 열어 봅니다.
# 레포 트리가 sys.path 에 없어야 폴백이 아니라 패키지 안을 보는지 확인됩니다.
uv venv --quiet "$work/venv"
uv pip install --quiet --python "$work/venv" "$wheel"

cd "$work"
"$work/venv/bin/python" - <<'PY'
from recsys_sql import load_catalog, prepare
from recsys_sql.config import get_settings

settings = get_settings()
if not settings.queries_dir.is_dir():
    raise SystemExit(f"실패: 설치된 패키지에서 카탈로그를 못 찾습니다: {settings.queries_dir}")

queries = load_catalog(settings.queries_dir)
sql, args = prepare("product_detail", {"product_id": 1})
if ":product_id" in sql or args != (1,):
    raise SystemExit("실패: 바인딩 변환이 동작하지 않습니다.")

print(f"설치된 휠에서 쿼리 {len(queries)}개 로드, 바인딩 변환 정상")
PY

echo "통과"
