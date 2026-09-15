"""서빙이 쓰는 repository 진입점.

`recsys_sql` 은 SQL 을 **검증하는 곳**이면서 동시에 **서빙이 실제로 쓰는 곳**입니다.
예전에는 검증이 끝난 .sql 을 `serving/sql/` 로 복사해 옮겼는데(promote), 복사본은
반드시 갈라집니다. 실제로 `user_fridge.ingredient_id` 를 걷어낼 때 recsys_sql 쪽만
고치고 serving 쪽 복사본은 그대로 남았습니다.

그래서 복사를 없앴습니다. 서빙은 이 모듈로 카탈로그를 조회하고, DB 연결만 자기 것을
씁니다. 폴더마다 다른 것은 `.env` 뿐입니다.

    recsys_sql  카탈로그(SQL + 파라미터 계약) + pytest 검증
    serving     asyncpg 풀 + 엔드포인트. SQL 은 여기서 가져다 씀

카탈로그(`queries/`)는 **패키지 안**에 있습니다. `.py` 와 똑같이 휠에 딸려 가므로
설치 형태(editable / --no-editable)와 무관하게 같은 경로가 잡힙니다.

## 바인딩 형식

카탈로그 SQL 은 `:name` 형식입니다(SQLAlchemy). 서빙은 asyncpg 를 직접 써서 `$1`
위치 파라미터가 필요합니다. 두 벌을 손으로 관리하면 또 갈라지므로 여기서 변환합니다.

    sql, args = bind_asyncpg(find_query("product_detail"), {"product_id": 101})
    rows = await conn.fetch(sql, *args)

변환 전에 `validate_params` 를 통과시키므로, 파라미터가 빠지거나 타입이 어긋나면
DB 까지 가지 않고 여기서 걸립니다.

이 모듈은 **SQLAlchemy 를 끌어오지 않습니다.** 서빙은 asyncpg 만 쓰기로 한 폴더라,
검증 하나 때문에 ORM 전체가 이미지에 들어가면 안 됩니다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from recsys_sql.catalog import CatalogError, SqlQuery, load_catalog, validate_params
from recsys_sql.config import QUERIES_DIR

# `:param` 은 잡고 `::text` 같은 캐스팅은 건너뜁니다. catalog 의 것과 같은 규칙입니다.
_BIND_PARAM = re.compile(r"(?<![:\w]):([a-zA-Z_]\w*)")

# 주석 줄은 변환 대상이 아닙니다. 헤더의 `-- params: user_id:int` 가 섞이면 안 됩니다.
_COMMENT_LINE = re.compile(r"^\s*--")


@lru_cache(maxsize=1)
def _catalog_by_name() -> dict[str, SqlQuery]:
    """카탈로그를 이름으로 찾을 수 있게 펴 둡니다. 프로세스당 한 번만 읽습니다."""
    return {query.name: query for query in load_catalog(QUERIES_DIR)}


def find_query(name: str) -> SqlQuery:
    """카탈로그에서 쿼리 하나를 찾습니다.

    `QUERY_OWNER` 와 무관하게 카탈로그 **전체**를 봅니다. 서빙은 누가 쓴 쿼리인지와
    상관없이 이름으로 찾아야 하기 때문입니다.

    `Settings` 를 거치지 않습니다. 카탈로그는 패키지 안에 있어서 경로가 설정과 무관하고,
    서빙이 이걸 부를 때 recsys_sql 의 `.env` 까지 읽을 이유가 없습니다.
    """
    catalog = _catalog_by_name()
    try:
        return catalog[name]
    except KeyError as exc:
        raise CatalogError(f"카탈로그에 없는 쿼리입니다: {name} ({sorted(catalog)})") from exc


def bind_asyncpg(query: SqlQuery, params: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """`:name` SQL 을 asyncpg 의 `$1` 형식과 인자 튜플로 바꿉니다.

    같은 이름이 여러 번 나오면 같은 번호를 다시 씁니다. asyncpg 는 `$1` 을 여러 곳에서
    참조해도 되므로 인자를 복제하지 않습니다.
    """
    validate_params(query, params)

    order: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in order:
            order.append(name)
        return f"${order.index(name) + 1}"

    converted = "\n".join(
        line if _COMMENT_LINE.match(line) else _BIND_PARAM.sub(replace, line) for line in query.sql.splitlines()
    )
    return converted, tuple(params[name] for name in order)


def prepare(name: str, params: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """이름으로 찾아 바로 asyncpg 형식으로 냅니다. 서빙에서 가장 많이 쓰는 한 줄입니다."""
    return bind_asyncpg(find_query(name), params)
