"""추천 비즈니스 로직 SQL 카탈로그.

서빙의 repository layer 이기도 합니다. SQL 을 복사해 가지 말고 여기서 가져다 쓰세요.

    from recsys_sql import prepare

    sql, args = prepare("product_detail", {"product_id": 101})
    rows = await conn.fetch(sql, *args)

자세한 이유와 사용법은 `recsys_sql.repository` 의 모듈 독스트링에 있습니다.
"""

from recsys_sql.catalog import CatalogError, SqlQuery, load_catalog
from recsys_sql.repository import bind_asyncpg, find_query, prepare

__all__ = [
    "CatalogError",
    "SqlQuery",
    "__version__",
    "bind_asyncpg",
    "find_query",
    "load_catalog",
    "prepare",
]

__version__ = "0.1.0"
