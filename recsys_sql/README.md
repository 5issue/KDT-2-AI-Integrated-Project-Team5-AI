# recsys_sql

추천 비즈니스 로직 SQL 을 **카탈로그**로 관리하고 **pytest 로 검증**하는 폴더입니다.

추천 로직은 대부분 SQL 안에 있는데, SQL 은 리뷰만으로는 맞는지 알기 어렵습니다.
"유통기한 지난 재료를 보유로 쳤다", "품절 상품을 추천했다" 같은 실수는 데이터를 넣고
돌려봐야 보입니다. 그래서 SQL 을 파일로 두고, 시드 데이터를 넣은 트랜잭션 위에서
규칙별로 단언하는 구조로 잡았습니다.

## 시작하기

```bash
cp recsys_sql/.env.example recsys_sql/.env   # 본인 Neon 브랜치 URL 을 넣기
uv sync --all-packages --all-groups
uv run recsys-sql check-db
uv run recsys-sql list
uv run pytest recsys_sql/tests -q
```

`DATABASE_URL` 이 비어 있으면 DB 테스트는 자동으로 skip 되고 정적 검증만 돕니다.
카탈로그 규약 검사는 DB 없이도 도니까 PR 올리기 전에 바로 확인할 수 있습니다.

## 3명이 나눠 쓰는 방법

```
queries/
├── _template/          예시 3종 (그대로 두기)
├── <github_id>/        각자 폴더
└── ...
```

본인 github id 로 폴더를 만들고 `.env` 의 `QUERY_OWNER` 에 같은 값을 넣으면 기본 대상이
본인 폴더로 잡힙니다. 폴더가 갈려 있어 세 명이 같은 파일을 건드릴 일이 없고,
`load_catalog` 이 이름 중복을 막아 주므로 머지할 때 쿼리 이름이 겹치면 바로 실패합니다.

DB 도 각자 Neon 브랜치를 씁니다. main 브랜치에 직접 붙지 마세요.
테스트가 롤백되더라도 시퀀스와 통계는 되돌아가지 않습니다.

## 쿼리 파일 규약

```sql
-- name: fridge_recipe_match
-- owner: openLeeWorld
-- description: 냉장고 재료로 만들 수 있는 레시피를 커버리지 순으로 추천
-- params: user_id:int, min_coverage:float, max_results:int

SELECT ...
```

카탈로그 로더가 자동으로 막는 것:

- 헤더 누락, 파일명과 `name` 불일치, 카탈로그 전체 이름 중복
- `params` 선언과 본문 `:파라미터` 불일치 (오타로 빠뜨린 바인딩을 잡습니다)
- 쓰기 구문(`INSERT`/`UPDATE`/`DELETE`/`DROP` 등) 혼입

값은 항상 바인딩으로 넘어갑니다. f-string 으로 값을 끼워 넣지 마세요(OWASP A03).
타입도 실행 전에 검사해서, `int` 자리에 `bool` 이 들어가는 것까지 막습니다.

## 예시 쿼리 3종

| 쿼리 | 시나리오 | 검증하는 규칙 |
| --- | --- | --- |
| `fridge_recipe_match` | 지금 만들 수 있는 레시피 | 유통기한 만료 재료 제외, 상비재료는 보유로 인정, 커버리지 하한, 조리시간 타이브레이크 |
| `missing_ingredient_products` | 부족한 재료를 채울 상품 | 레시피 지정 상품 우선, 인기도 > 가격, 비활성/품절 제외, 재료별 상한 |
| `reorder_candidates` | 재구매 후보 | 최근 구매 제외, 냉장고에 멀쩡히 있으면 제외, 만료됐으면 후보로 인정 |

## 테스트 구조

| 파일 | DB 필요 | 하는 일 |
| --- | --- | --- |
| `tests/test_catalog.py` | 아니오 | 헤더/파라미터/읽기전용 규약 검증 |
| `tests/test_runner.py` | 아니오 | 파라미터 타입 검증 |
| `tests/test_db_connection.py` | 예 | Neon 연결, 테이블 존재, 트랜잭션 격리 |
| `tests/test_recommendation_queries.py` | 예 | 시드 데이터 위에서 추천 규칙 단언 + `EXPLAIN` 점검 |

DB 테스트는 `@pytest.mark.db` 가 붙어 있고, `db_conn` 픽스처가 트랜잭션을 열었다가
끝나면 무조건 롤백합니다. 시드 데이터의 PK 는 실제 데이터와 겹치지 않게 90억 대역을 씁니다.

`EXPLAIN` 점검은 `.env` 의 `FORBID_SEQ_SCAN_ON` 에 적은 테이블에 Seq Scan 이 걸리면 실패합니다.
데이터가 적은 초기에는 플래너가 Seq Scan 을 고르는 게 정상이니, 데이터를 채운 뒤부터
켜는 편이 낫습니다. 잠시 끄려면 그 값을 비워두세요.

## 직접 돌려보기

```bash
uv run recsys-sql run --query fridge_recipe_match \
    --param user_id=1 --param min_coverage=0.5 --param max_results=5

uv run recsys-sql explain --query fridge_recipe_match \
    --param user_id=1 --param min_coverage=0.5 --param max_results=5
```

레포 루트에 `recsys_sql/` 디렉터리가 있어서 `python -m recsys_sql.cli` 는 그 디렉터리를
네임스페이스 패키지로 잡아 실패합니다. 위처럼 콘솔 스크립트를 쓰세요.
