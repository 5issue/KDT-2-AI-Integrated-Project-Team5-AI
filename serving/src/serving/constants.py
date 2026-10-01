"""serving 공통 상수. 파일마다 흩어지지 않게 한 곳에 둡니다."""

from __future__ import annotations

API_PREFIX = "/api/v1"

# id 컬럼(bigint)이 담을 수 있는 최댓값. 경로·본문·사용자 id 의 상한으로 겁니다. 이보다 큰 값은 asyncpg 가
# 인자를 인코딩하다 DataError(500)를 내므로, 요청 단계에서 422(사용자 id 는 401)로 막습니다.
PG_BIGINT_MAX = 2**63 - 1
