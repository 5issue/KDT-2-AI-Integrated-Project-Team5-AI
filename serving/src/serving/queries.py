"""엔드포인트가 쓰는 SQL 로딩.

SQL 은 `serving/sql/` 에 파일로 둡니다. 파이썬 문자열로 흩어 두면 리뷰가 어렵고,
`recsys_sql` 에서 검증한 쿼리를 그대로 옮겨 오기도 번거롭기 때문입니다.
바인딩은 asyncpg 의 위치 파라미터($1, $2)를 씁니다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from serving.config import get_settings

# 서빙에서 노출하는 쿼리 화이트리스트. 여기 없는 파일은 로드하지 않습니다.
ALLOWED_QUERIES = frozenset({"fridge_recipe_match", "reorder_candidates"})


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    """sql/<name>.sql 을 읽습니다. 파일은 프로세스당 한 번만 읽습니다."""
    if name not in ALLOWED_QUERIES:
        raise KeyError(f"허용되지 않은 쿼리입니다: {name}")
    path: Path = get_settings().sql_dir / f"{name}.sql"
    if not path.exists():
        raise FileNotFoundError(f"SQL 파일이 없습니다: {path.name}")
    return path.read_text(encoding="utf-8")
