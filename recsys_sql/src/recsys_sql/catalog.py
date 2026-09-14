"""추천 SQL 카탈로그.

`queries/<github_id>/<name>.sql` 파일 하나가 쿼리 하나입니다.
파일 맨 위 주석에 메타데이터를 적어 두면 여기서 읽어 들여 pytest 로 검증합니다.

    -- name: fridge_recipe_match
    -- owner: openLeeWorld
    -- description: 냉장고 재료로 만들 수 있는 레시피를 커버리지 순으로 추천
    -- params: user_id:int, min_coverage:float, max_results:int

바인딩은 SQLAlchemy 의 이름 있는 파라미터(`:user_id`)를 씁니다. 문자열 포매팅으로
값을 끼워 넣지 않기 때문에 SQL 인젝션 경로가 생기지 않습니다(OWASP A03).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recsys_sql.config import Settings, get_settings

_META_LINE = re.compile(r"^\s*--\s*(?P<key>name|owner|description|params|tags)\s*:\s*(?P<value>.*)$")

# `:param` 은 잡고 `::text` 같은 캐스팅은 건너뜁니다.
_BIND_PARAM = re.compile(r"(?<![:\w]):([a-zA-Z_]\w*)")

_ALLOWED_PARAM_TYPES = frozenset({"int", "float", "str", "bool", "date", "list[int]", "list[str]"})

# 헤더 줄의 `-- name:` 같은 표기는 바인딩이 아닙니다.
_META_KEYS = frozenset({"name", "owner", "description", "params", "tags"})

# 카탈로그 SQL 은 읽기 전용이어야 합니다. 쓰기 구문이 들어오면 검증에서 막습니다.
_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COPY)\b",
    re.IGNORECASE,
)


class CatalogError(ValueError):
    """SQL 파일의 메타데이터나 본문이 규약을 어겼을 때."""


@dataclass(slots=True, frozen=True)
class SqlQuery:
    """카탈로그에 등록된 쿼리 하나."""

    name: str
    owner: str
    description: str
    params: dict[str, str]
    sql: str
    path: Path

    @property
    def bind_names(self) -> set[str]:
        """SQL 본문에 실제로 등장하는 바인딩 파라미터 이름."""
        return set(_BIND_PARAM.findall(_strip_comments(self.sql)))


def _strip_comments(sql: str) -> str:
    """`--` 주석과 `/* */` 블록을 지웁니다. 메타데이터 주석이 본문 검사에 섞이지 않게 합니다."""
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_block)


def _parse_params(raw: str) -> dict[str, str]:
    """`user_id:int, limit:int` 형식을 dict 로 바꿉니다."""
    params: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise CatalogError(f"params 항목에 타입이 없습니다: {chunk!r} (예: user_id:int)")
        name, _, type_name = chunk.partition(":")
        name, type_name = name.strip(), type_name.strip()
        if type_name not in _ALLOWED_PARAM_TYPES:
            raise CatalogError(f"지원하지 않는 파라미터 타입입니다: {type_name!r} ({sorted(_ALLOWED_PARAM_TYPES)})")
        params[name] = type_name
    return params


# 파라미터 타입 이름 -> 받아 줄 파이썬 타입.
# SQLAlchemy 를 끌어오지 않고도 검증할 수 있어야 해서 여기 둡니다. 서빙은 asyncpg 만 씁니다.
_PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "int": (int,),
    "float": (int, float),
    "str": (str,),
    "bool": (bool,),
    "date": (str,),
    "list[int]": (list, tuple),
    "list[str]": (list, tuple),
}


def validate_params(query: SqlQuery, params: dict[str, Any]) -> dict[str, Any]:
    """선언된 파라미터가 다 왔는지, 타입이 맞는지 확인합니다."""
    if missing := sorted(set(query.params) - set(params)):
        raise CatalogError(f"{query.name}: 파라미터가 빠졌습니다: {missing}")
    if extra := sorted(set(params) - set(query.params)):
        raise CatalogError(f"{query.name}: 선언되지 않은 파라미터입니다: {extra}")

    for name, type_name in query.params.items():
        _check_value(query.name, name, type_name, params[name])
    return params


# 리스트 선언의 원소 타입.
_ELEMENT_TYPES: dict[str, str] = {"list[int]": "int", "list[str]": "str"}


def _check_value(query_name: str, name: str, type_name: str, value: Any) -> None:
    """값 하나가 선언한 타입과 맞는지 봅니다."""
    # 파이썬에서 bool 은 int 의 서브클래스입니다. 숫자 자리에 True 가 들어가면
    # 여기서 안 막을 때 DB 까지 가서 1 로 바뀝니다. int 와 float 둘 다 막습니다.
    if type_name in {"int", "float"} and isinstance(value, bool):
        raise CatalogError(f"{query_name}.{name}: {type_name} 자리에 bool 이 들어왔습니다.")

    if not isinstance(value, _PYTHON_TYPES[type_name]):
        raise CatalogError(f"{query_name}.{name}: {type_name} 을 기대했지만 {type(value).__name__} 입니다.")

    # 리스트는 껍데기만 보면 부족합니다. `list[int]` 에 문자열이 담겨 와도 통과해 버리고,
    # 그 값은 asyncpg 바인딩 시점에 가서야 터집니다.
    element_type = _ELEMENT_TYPES.get(type_name)
    if element_type is None:
        return
    for index, item in enumerate(value):
        if element_type == "int" and isinstance(item, bool):
            raise CatalogError(f"{query_name}.{name}[{index}]: int 자리에 bool 이 들어왔습니다.")
        if not isinstance(item, _PYTHON_TYPES[element_type]):
            raise CatalogError(
                f"{query_name}.{name}[{index}]: {element_type} 을 기대했지만 {type(item).__name__} 입니다."
            )


def load_query(path: Path) -> SqlQuery:
    """.sql 파일 하나를 읽어 메타데이터까지 검증합니다."""
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    for line in text.splitlines():
        match = _META_LINE.match(line)
        if match:
            meta[match.group("key")] = match.group("value").strip()
        elif line.strip() and not line.lstrip().startswith("--"):
            break

    missing = [key for key in ("name", "owner", "description") if not meta.get(key)]
    if missing:
        raise CatalogError(f"{path.name}: 헤더에 {', '.join(missing)} 이(가) 없습니다.")

    query = SqlQuery(
        name=meta["name"],
        owner=meta["owner"],
        description=meta["description"],
        params=_parse_params(meta.get("params", "")),
        sql=text,
        path=path,
    )

    if query.name != path.stem:
        raise CatalogError(f"{path.name}: name({query.name}) 과 파일명({path.stem})이 다릅니다.")

    declared, used = set(query.params), query.bind_names
    if undeclared := used - declared:
        raise CatalogError(f"{path.name}: 본문에 쓰였지만 params 에 없는 파라미터: {sorted(undeclared)}")
    if unused := declared - used:
        raise CatalogError(f"{path.name}: params 에 있지만 본문에 없는 파라미터: {sorted(unused)}")

    if match := _WRITE_KEYWORDS.search(_strip_comments(text)):
        raise CatalogError(f"{path.name}: 카탈로그 쿼리는 읽기 전용이어야 합니다 ({match.group(0)} 발견).")

    # SQLAlchemy 의 `text()` 는 주석 안까지 훑어 `:이름` 을 바인딩으로 잡습니다.
    # 위 검사는 주석을 지우고 보기 때문에, 설명에 `:w1` 같은 표기를 적어 두면
    # 여기서는 통과하고 실행 시점에 "값이 없다" 로 터집니다. 실제로 한 번 밟았습니다.
    body_only = {name for name in _BIND_PARAM.findall(text) if name not in _META_KEYS}
    if in_comment := body_only - used - declared:
        raise CatalogError(
            f"{path.name}: 주석에 바인딩처럼 보이는 표기가 있습니다: {sorted(in_comment)}. "
            "설명에는 콜론 대신 다른 표기를 쓰세요."
        )

    return query


def load_catalog(root: Path | None = None, settings: Settings | None = None) -> list[SqlQuery]:
    """폴더 아래 모든 .sql 을 읽습니다. 이름이 중복되면 실패시킵니다."""
    settings = settings or get_settings()
    root = root or settings.owner_dir
    if not root.exists():
        raise FileNotFoundError(f"쿼리 폴더가 없습니다: {root}")

    queries: list[SqlQuery] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.rglob("*.sql")):
        query = load_query(path)
        if query.name in seen:
            raise CatalogError(f"쿼리 이름이 중복입니다: {query.name} ({seen[query.name].name}, {path.name})")
        seen[query.name] = path
        queries.append(query)
    return queries


def find_query(name: str, settings: Settings | None = None) -> SqlQuery:
    """이름으로 쿼리 하나를 찾습니다. 카탈로그 전체를 대상으로 봅니다."""
    settings = settings or get_settings()
    for query in load_catalog(settings.queries_dir, settings):
        if query.name == name:
            return query
    raise KeyError(f"카탈로그에 없는 쿼리입니다: {name}")
