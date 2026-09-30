"""찜한 레시피 / 최근 본 레시피 쓰기 SQL.

카탈로그(recsys_sql)는 읽기 전용이라 쓰기문은 서빙이 갖습니다. `fridge_sql` 과 같은 이유로
파이썬 상수이고, 전부 asyncpg 위치 파라미터입니다. 모든 문장에 user_id 조건을 강제해
타인 기록 접근(IDOR)을 막습니다.

레시피 존재 확인을 위해 상세 쿼리를 먼저 돌리지 않습니다. FK 위반을 라우터가 받아
404 로 바꾸는 편이 왕복 한 번이 적고, 확인과 삽입 사이의 경쟁도 없습니다.
"""

from __future__ import annotations

# 이미 찜한 레시피는 0행이라 라우터가 409 로 바꿉니다.
INSERT_FAVORITE = """
INSERT INTO user_recipe_favorite (user_id, recipe_id)
VALUES ($1, $2)
ON CONFLICT DO NOTHING
RETURNING created_at
"""

DELETE_FAVORITE = "DELETE FROM user_recipe_favorite WHERE user_id = $1 AND recipe_id = $2 RETURNING recipe_id"

# 같은 레시피를 다시 보면 행을 늘리지 않고 viewed_at 만 당깁니다.
UPSERT_VIEW = """
INSERT INTO user_recipe_view (user_id, recipe_id, viewed_at)
VALUES ($1, $2, NOW())
ON CONFLICT ON CONSTRAINT pk_user_recipe_view
DO UPDATE SET viewed_at = EXCLUDED.viewed_at
RETURNING viewed_at
"""

# 사용자당 최근 N 건만 남깁니다. 오래 쓴 계정의 조회 기록이 끝없이 쌓이지 않게 하는
# 유일한 장치라, UPSERT 와 같은 트랜잭션에서 돌립니다.
TRIM_VIEWS = """
DELETE FROM user_recipe_view
WHERE user_id = $1
  AND recipe_id NOT IN (
      SELECT recipe_id
      FROM user_recipe_view
      WHERE user_id = $1
      ORDER BY viewed_at DESC, recipe_id DESC
      LIMIT $2
  )
"""

# 최근 본 레시피 선택 삭제. 화면의 "전체선택 -> 선택삭제" 가 체크된 id 배열을 한 번에 보냅니다.
# 기록에 없는 id 는 조용히 건너뛰고, 실제로 지운 id 만 돌려줍니다.
DELETE_VIEWS = """
DELETE FROM user_recipe_view
WHERE user_id = $1
  AND recipe_id = ANY($2::bigint[])
RETURNING recipe_id
"""
