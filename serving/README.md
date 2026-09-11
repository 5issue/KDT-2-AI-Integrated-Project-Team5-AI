# serving

FastAPI + asyncpg 비동기 서빙 서버입니다. **SQL 은 `recsys_sql` 카탈로그에서 가져옵니다.**

SQLAlchemy 를 거치지 않고 asyncpg 를 직접 씁니다. 서빙 경로에서는 ORM 매핑 비용 없이
정해진 SQL 만 돌리면 되기 때문입니다.

## 이 폴더는 .sql 파일을 갖지 않습니다

예전에는 `recsys_sql` 에서 검증한 쿼리를 `serving/sql/` 로 복사해 왔습니다(promote).
복사본은 반드시 갈라집니다. 실제로 `user_fridge.ingredient_id` 를 걷어낼 때 recsys_sql
쪽만 고쳐지고 이쪽 복사본은 사라진 컬럼을 그대로 참조한 채 남았습니다.

지금은 `recsys_sql` 을 워크스페이스 의존성으로 두고 카탈로그를 직접 씁니다.
두 폴더가 다른 것은 `.env` 뿐입니다.

```python
from serving.queries import build_query

sql, args = build_query("product_detail", {"product_id": 101})
async with pool.acquire() as conn:
    rows = await conn.fetch(sql, *args)
```

`build_query` 가 하는 일:

1. `ALLOWED_QUERIES` 화이트리스트 확인 — 무엇을 공개하는지 한 곳에 적혀 있어야 합니다
2. `recsys_sql.prepare` 호출 — 파라미터 검증 + `:name` -> `$1` 변환

파라미터가 빠지거나 타입이 어긋나면 DB 까지 가지 않고 걸립니다. 카탈로그에서 쿼리가
사라지거나 이름이 바뀌면 `tests/test_app.py` 가 배포 전에 잡습니다.

**`recsys_sql.repository` 는 SQLAlchemy 를 끌어오지 않습니다.** 이 폴더가 asyncpg 만
쓰기로 한 결정은 그대로입니다. `tests/test_app.py` 가 별도 프로세스에서 고정합니다.

### 새 엔드포인트를 붙이려면

1. `recsys_sql/queries/<owner>/` 에 SQL 을 쓰고 pytest 로 검증 (그쪽 README 참고)
2. `serving/queries.py` 의 `ALLOWED_QUERIES` 에 이름 추가
3. 라우터에서 `build_query(이름, 파라미터)` 호출
4. `schemas.py` 에 응답 모델 추가

SQL 을 이 폴더로 복사하지 마세요. `tests/test_app.py` 가 `.sql` 파일이 생기면 실패합니다.

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

`ai_context/api_spec.md` 의 나머지 엔드포인트는 아직 라우터가 없습니다. 받칠 SQL 은
카탈로그에 이미 있습니다 — `recsys_sql/README.md` 의 대응표를 보세요.

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
| `tests/test_app.py` | 아니오 | 라우팅, 503 처리, 입력 검증, 카탈로그 계약, .sql 복사본 금지 |
| `tests/test_endpoints_db.py` | 예 | 실제 Neon 에서 카탈로그 SQL 실행 + 응답 스키마 |

# fastapi 배포

기본적으로 Dockerfile로 만들어서 AWS EKS에 배포할 계획
