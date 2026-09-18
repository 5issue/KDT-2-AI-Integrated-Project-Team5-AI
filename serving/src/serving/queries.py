"""엔드포인트가 쓰는 SQL 로딩.

SQL 은 `recsys_sql` 카탈로그에서 가져옵니다. **serving 은 .sql 파일을 갖지 않습니다.**

예전에는 검증이 끝난 쿼리를 `serving/sql/` 로 복사해 왔는데(promote), 복사본은 반드시
갈라집니다. 실제로 `user_fridge.ingredient_id` 를 걷어낼 때 recsys_sql 쪽만 고쳐지고
serving 쪽 복사본은 옛 컬럼을 그대로 참조한 채 남았습니다.

이제 두 폴더가 다른 것은 `.env` 뿐입니다. recsys_sql 은 SQL 과 파라미터 계약을 갖고,
serving 은 asyncpg 풀과 엔드포인트를 갖습니다.

바인딩 변환(`:name` -> `$1`)도 recsys_sql 이 합니다. 파라미터가 빠지거나 타입이
어긋나면 DB 까지 가지 않고 거기서 걸립니다.
"""

from __future__ import annotations

from typing import Any

from recsys_sql import CatalogError, prepare

# 서빙에서 노출하는 쿼리 화이트리스트.
#
# 카탈로그에는 실험 중인 쿼리도 섞여 있습니다. 엔드포인트가 이름을 그대로 받아 넘기는
# 구조가 아니더라도, 무엇을 공개하는지는 한 곳에 적혀 있어야 합니다.
ALLOWED_QUERIES = frozenset(
    {
        "bubble_candidate_counts",
        "bubble_products",
        "bubble_recipe_candidates",
        "fridge_recipe_match",
        "missing_ingredient_products",
        "my_fridge_items",
        "my_recipe_candidates",
        "product_detail",
        "product_recipes",
        "product_storage_guideline",
        "recipe_detail",
        "recipe_missing_ingredients",
    }
)


def build_query(name: str, params: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """카탈로그 쿼리를 asyncpg 가 바로 받을 수 있는 (sql, args) 로 만듭니다."""
    if name not in ALLOWED_QUERIES:
        raise KeyError(f"허용되지 않은 쿼리입니다: {name}")
    try:
        return prepare(name, params)
    except CatalogError as exc:
        # 카탈로그 계약이 깨진 것은 요청 잘못이 아니라 배포 잘못입니다.
        raise RuntimeError(f"{name}: 카탈로그 계약과 맞지 않습니다. {exc}") from exc
