"""My냉장고 쓰기 SQL.

카탈로그(recsys_sql)는 읽기 전용 원칙이라 쓰기문은 서빙이 갖습니다. serving 은
.sql 파일을 두지 않는 규칙(카탈로그 복사본이 갈라지는 문제의 재발 방지)이 있어
파이썬 상수로 관리합니다 — 쓰기문 전체가 이 모듈 하나에서 버전 관리됩니다.

전부 asyncpg 위치 파라미터이고, 모든 문장에 user_id 조건을 강제해 타인 품목
접근(IDOR)을 막습니다.
"""

from __future__ import annotations

# 상품의 PRIMARY 재료 수만큼 행을 만듭니다. 재료가 연결되지 않은 상품은 0행이라
# 담기지 않습니다 (ingredient_id 가 NOT NULL 이라 담을 방법이 없습니다).
INSERT_ITEM = """
INSERT INTO user_fridge (ingredient_id, user_id, product_id, quantity, unit, expires_at)
SELECT pi.ingredient_id, $1, $2, $3, $4, $5
FROM product_ingredient pi
WHERE pi.product_id = $2
  AND pi.role = 'PRIMARY'
ON CONFLICT DO NOTHING
RETURNING ingredient_id
"""

EXISTS_ITEM = "SELECT 1 FROM user_fridge WHERE user_id = $1 AND product_id = $2 LIMIT 1"

# 읽고-다시-쓰는 방식은 동시 요청이 서로의 변경을 덮어쓸 수 있어 단일 조건부
# UPDATE 로 처리합니다. quantity/unit 의 NULL 파라미터는 "미변경"($3, $4),
# expires_at 은 NULL 이 유효값(기한 없음)이라 변경 여부 플래그($6)로 구분합니다.
UPDATE_ITEM = """
UPDATE user_fridge
SET quantity = COALESCE($3, quantity),
    unit = COALESCE($4, unit),
    expires_at = CASE WHEN $6 THEN $5 ELSE expires_at END
WHERE user_id = $1 AND product_id = $2
RETURNING quantity, unit, expires_at
"""

DELETE_ITEM = "DELETE FROM user_fridge WHERE user_id = $1 AND product_id = $2 RETURNING product_id"
