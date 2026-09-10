# serving

FastAPI + asyncpg 비동기 서빙 서버입니다. `recsys_sql` 에서 검증한 추천 쿼리를 API 로 노출합니다.

SQLAlchemy 를 거치지 않고 asyncpg 를 직접 씁니다. 서빙 경로에서는 ORM 매핑 비용 없이
정해진 SQL 만 돌리면 되기 때문입니다.

## 시작하기

```bash
cp serving/.env.example serving/.env
uv sync --all-packages --all-groups
uv run serving check-db     # 풀을 만들어 왕복 한 번 확인
uv run serving run          # http://127.0.0.1:8000 (자동 리로드)
```

`ENVIRONMENT=local` 일 때만 `/docs` 와 `/openapi.json` 이 열립니다.

## 엔드포인트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/health` | liveness. DB 를 건드리지 않습니다. |
| GET | `/health/db` | readiness. 풀에서 커넥션을 빌려 왕복 한 번. 실패 시 503 |
| GET | `/users/{user_id}/recipe-recommendations` | 냉장고 재료 기반 레시피 추천 |
| GET | `/users/{user_id}/reorder-candidates` | 재구매 후보 |

liveness 와 readiness 를 나눈 이유는, DB 가 잠깐 흔들릴 때 컨테이너가 통째로
재시작되지 않게 하기 위해서입니다.

## 커넥션 풀

풀은 lifespan 에서 한 번 만들고 앱 상태에 붙입니다. 요청마다 새로 붙으면 Neon 의
커넥션 한도를 금방 넘습니다.

- **pooler 엔드포인트를 씁니다.** Neon 은 커넥션 수 제한이 빡빡해서, 서버리스나 멀티 워커
  환경에서는 PgBouncer 를 거치는 pooler 가 사실상 필수입니다.
- **pooler 에서는 statement 캐시를 끕니다.** PgBouncer transaction 모드는 요청마다 다른
  백엔드에 붙을 수 있어 prepared statement 를 재사용할 수 없습니다. `db.normalize_neon_dsn`
  이 호스트에 `-pooler` 가 있으면 자동으로 꺼 줍니다.
- **`DB_POOL_MAX_SIZE` 는 워커 하나당 값입니다.** `uvicorn --workers 4` 면 실제 커넥션은 4배입니다.
- **`DB_COMMAND_TIMEOUT`** 으로 느린 쿼리가 워커를 붙잡는 것을 막습니다.

`DATABASE_URL` 이 없으면 풀 없이 뜹니다. DB 없이도 앱을 띄워 라우팅과 스키마를
확인할 수 있게 하기 위한 것이고, 이때 DB 가 필요한 엔드포인트는 503 으로 답합니다.

## SQL 관리

SQL 은 `serving/sql/` 에 파일로 둡니다. 파이썬 문자열로 흩어 두면 리뷰가 어렵습니다.

```
recsys_sql/queries/<owner>/*.sql   ← pytest 로 규칙을 검증하는 곳 (:name 바인딩)
            │  검증 통과 후 promote
            ▼
serving/sql/*.sql                  ← 서빙이 실제로 쓰는 것 ($1 위치 바인딩)
```

파일 헤더의 `promoted-from` 이 원본 경로를 가리킵니다. 바인딩 표기가 다른 이유는
SQLAlchemy 는 이름 바인딩을, asyncpg 는 위치 바인딩을 쓰기 때문입니다.
`tests/test_app.py` 가 옮겨온 SQL 에 이름 바인딩이 남아 있지 않은지 확인합니다.

`queries.ALLOWED_QUERIES` 화이트리스트에 없는 이름은 로드되지 않습니다. 경로가
사용자 입력에서 오지 않더라도, 파일 로딩에 화이트리스트를 두는 편이 안전합니다.

## 보안 관련

- 값은 전부 asyncpg 위치 파라미터로 넘어갑니다. SQL 문자열에 값을 끼워 넣지 않습니다.
- 쿼리 파라미터는 FastAPI 에서 범위까지 검증합니다(`limit` 최대 50, `min_coverage` 0~1 등).
  DB 까지 가기 전에 422 로 걸립니다.
- 로그와 헬스 응답에 호스트/자격증명을 남기지 않습니다. DSN 은 `mask_dsn` 으로 가리고,
  연결 실패는 메시지 대신 예외 타입만 남깁니다.
- `ENVIRONMENT` 가 `local` 이 아니면 `/docs` 와 `/openapi.json` 을 닫습니다.

## 테스트

```bash
uv run pytest serving/tests -q
```

| 파일 | DB 필요 | 하는 일 |
| --- | --- | --- |
| `tests/test_dsn.py` | 아니오 | DSN 변환, pooler 감지, 마스킹 |
| `tests/test_app.py` | 아니오 | 라우팅, 503 처리, 입력 검증, SQL 화이트리스트 |
| `tests/test_endpoints_db.py` | 예 | 실제 Neon 에서 promoted SQL 실행 + 응답 스키마 |

# fastapi 배포

기본적으로 Dockerfile로 만들어서 AWS EKS에 배포할 계획
