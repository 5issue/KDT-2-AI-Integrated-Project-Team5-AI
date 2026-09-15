# recsys_sql

추천 비즈니스 로직 SQL 의 **단일 출처**입니다. 여기서 쓰고, 여기서 검증하고,
**서빙이 여기서 그대로 가져다 씁니다.**

추천 로직은 대부분 SQL 안에 있는데, SQL 은 리뷰만으로는 맞는지 알기 어렵습니다.
"유통기한 지난 재료를 보유로 쳤다", "품절 상품을 추천했다" 같은 실수는 데이터를 넣고
돌려봐야 보입니다. 그래서 SQL 을 파일로 두고, 실제 적재분이나 시드 데이터 위에서
규칙별로 단언하는 구조로 잡았습니다.

## serving 의 repository layer 입니다

예전에는 검증이 끝난 `.sql` 을 `serving/sql/` 로 복사해 옮겼습니다(promote).
복사본은 반드시 갈라집니다. 실제로 `user_fridge.ingredient_id` 를 걷어낼 때 이쪽만
고쳐지고 serving 쪽 복사본은 사라진 컬럼을 그대로 참조한 채 남았습니다.

복사를 없앴습니다. **`serving` 은 `.sql` 파일을 하나도 갖지 않습니다.**

```python
from serving.queries import build_query  # serving 쪽 화이트리스트를 거쳐

sql, args = build_query("product_detail", {"product_id": 101})
rows = await conn.fetch(sql, *args)  # serving 자기 asyncpg 풀로 실행
```

```python
from recsys_sql import prepare  # 직접 쓸 때

sql, args = prepare("product_detail", {"product_id": 101})
```

`prepare` 는 `Settings` 를 거치지 않습니다. 카탈로그가 패키지 안에 있어 경로가 설정과
무관하고, 서빙이 이걸 부를 때 `recsys_sql/.env` 까지 읽을 이유가 없습니다.

`prepare` 가 하는 일은 둘입니다.

1. **파라미터 검증** — 빠졌거나 타입이 어긋나면 DB 까지 가지 않고 여기서 걸립니다.
2. **바인딩 변환** — 카탈로그는 `:name`(SQLAlchemy), 서빙은 `$1`(asyncpg) 입니다.
   두 벌을 손으로 관리하면 또 갈라지므로 `recsys_sql.repository` 가 변환합니다.

그래서 두 폴더가 다른 것은 `.env` 뿐입니다. 이 폴더는 검증용 DB 를, 서빙은 서빙용 DB 를
봅니다. 같은 SQL 이 양쪽에서 그대로 돕니다.

`recsys_sql.repository` 는 **SQLAlchemy 를 끌어오지 않습니다.** 서빙은 asyncpg 만
쓰기로 한 폴더라, 검증 하나 때문에 ORM 전체가 이미지에 들어가면 안 됩니다.
`serving/tests/test_app.py` 가 이걸 별도 프로세스에서 고정합니다.

```
recsys_sql   SQL + 파라미터 계약 + 검증        <- 여기가 진짜 소스
     │
     │ import (복사 아님)
     ▼
serving      asyncpg 풀 + FastAPI 엔드포인트
```

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

**주의:** skip 은 조용합니다. `.env` 를 안 채운 채로 "통과" 를 봤다면 DB 쿼리는 한 줄도
안 돌았을 수 있습니다. 실제로 버블 테스트 5개가 그렇게 몇 주간 죽어 있었습니다.
`-rs` 를 붙여 skip 사유를 확인하세요.

## 폴더 나눠 쓰기

```
src/recsys_sql/queries/
├── _template/          예시 3종 (그대로 두기)
├── <github_id>/        각자 폴더
└── ...
```

**패키지 안에 있는 것이 중요합니다.** 예전에는 `recsys_sql/queries/` 였는데, 패키지
밖이라 휠에 담기지 않았습니다. 개발 중에는 `uv sync` 가 editable 로 설치해 `__file__` 이
레포 안을 가리키므로 멀쩡해 보이고, 이미지를 만드는 순간(`--no-editable`) 첫 요청에서
`FileNotFoundError` 로 죽습니다. 지금은 `.py` 와 똑같이 딸려 가므로 hatch 설정도,
경로 폴백도 필요 없습니다.

본인 github id 로 폴더를 만들고 `.env` 의 `QUERY_OWNER` 에 같은 값을 넣으면 CLI 의 기본
대상이 본인 폴더로 잡힙니다. `load_catalog` 이 이름 중복을 막아 주므로 머지할 때 쿼리
이름이 겹치면 바로 실패합니다.

**서빙은 `QUERY_OWNER` 와 무관하게 카탈로그 전체를 봅니다.** 누가 쓴 쿼리인지와 상관없이
이름으로 찾아야 하기 때문입니다. 그래서 쿼리 이름은 폴더가 달라도 레포 전체에서 유일합니다.

DB 도 각자 Neon 브랜치를 씁니다. main 브랜치에 직접 붙지 마세요.
테스트가 롤백되더라도 시퀀스와 통계는 되돌아가지 않습니다.

## 쿼리 파일 규약

```sql
-- name: product_detail
-- owner: openLeeWorld
-- description: 상품 상세. 구성 재료를 한 줄에 묶어 함께 냅니다
-- params: product_id:int

SELECT ...
```

카탈로그 로더가 자동으로 막는 것:

- 헤더 누락, 파일명과 `name` 불일치, 카탈로그 전체 이름 중복
- `params` 선언과 본문 `:파라미터` 불일치 (오타로 빠뜨린 바인딩을 잡습니다)
- **주석 안의 `:표기`** — SQLAlchemy 는 주석까지 훑어 바인딩으로 잡습니다.
  설명에 `:w1` 같은 걸 적어 두면 실행 시점에야 "값이 없다" 로 터집니다
- 쓰기 구문(`INSERT`/`UPDATE`/`DELETE`/`DROP` 등) 혼입

값은 항상 바인딩으로 넘어갑니다. f-string 으로 값을 끼워 넣지 마세요(OWASP A03).
타입도 실행 전에 검사해서, `int` 자리에 `bool` 이 들어가는 것까지 막습니다.

## 카탈로그 현황

`queries/openLeeWorld/` — `ai_context/api_spec.md` 의 읽기 엔드포인트를 받칩니다.

| 쿼리 | 엔드포인트 |
| --- | --- |
| `bubble_candidate_counts` | `GET /home/bubbles` |
| `bubble_recipe_candidates` | 버블 -> 레시피 목록 |
| `bubble_products` | `GET /recommendations/products` |
| `product_detail` | `GET /products/{id}` |
| `product_recipes` | `GET /products/{id}/recipes` |
| `product_storage_guideline` | `GET /products/{id}/storage-guide` |
| `recipe_detail` | `GET /recipes/{id}` |
| `recipe_missing_ingredients` | `GET /recipes/{id}/missing-ingredients` |
| `my_fridge_items` | `GET /users/me/fridge` |
| `my_recipe_candidates` | `GET /recommendations/my-recipes` |

`queries/_template/` — 예시 3종. 규약을 보려면 여기부터 읽으세요.

| 쿼리 | 검증하는 규칙 |
| --- | --- |
| `fridge_recipe_match` | 만료 재료 제외, 상비재료는 보유로 인정, 커버리지 하한, 조리시간 타이브레이크 |
| `missing_ingredient_products` | 레시피 지정 상품 우선, 인기도 > 가격, 비활성/품절 제외, 재료별 상한 |
| `reorder_candidates` | 최근 구매 제외, 냉장고에 멀쩡히 있으면 제외, 만료됐으면 후보로 인정 |

### 알아 둘 것 하나: 냉장고는 재료가 아니라 상품을 담습니다

사용자가 넣는 것은 `한돈 삼겹살 500g` 이지 `돼지고기 > 삼겹살` 이 아닙니다.
재료는 `product_ingredient(role='PRIMARY')` 로 풉니다. 부재료까지 보유로 치면
밀키트 하나로 커버리지가 부풀어 만들 수 없는 레시피가 추천됩니다.

### 알아 둘 것 둘: 버블 규칙은 뷰 하나에만 있습니다

`bubble_keyword.rule_spec` 해석이 세 쿼리에 흩어져 있었고 이미 조금씩 달랐습니다.
세기로는 통과하는데 눌렀을 때 다른 목록이 나오는 상태입니다.
지금은 `bubble_recipe_candidate` 뷰(alembic `0008`) 한 곳에만 있습니다.
**새 버블 규칙(`rule_type`)을 추가하려면 그 뷰를 고쳐야 합니다.**

## 테스트 구조

| 파일 | DB 필요 | 하는 일 |
| --- | --- | --- |
| `tests/test_catalog.py` | 아니오 | 헤더/파라미터/읽기전용 규약 |
| `tests/test_runner.py` | 아니오 | 파라미터 타입 검증 |
| `tests/test_repository.py` | 아니오 | `:name` -> `$1` 변환, 카탈로그 전체가 서빙에서 바인딩되는지 |
| `tests/test_recsys_db_connection.py` | 예 | Neon 연결, 테이블 존재, 트랜잭션 격리 |
| `tests/test_recommendation_queries.py` | 예 | 시드 데이터 위 추천 규칙 + `EXPLAIN` 점검 |
| `tests/test_api_queries.py` | 예 | api_spec 엔드포인트 쿼리 (버블/상품/레시피/냉장고) |
| `tests/test_bubble_queries.py` | 예 | 버블 `min_candidates` 와 규칙 일치 |
| `tests/test_storage_guideline_query.py` | 예 | 보관법 (PRIMARY 하나일 때만) |

DB 테스트는 `@pytest.mark.db` 가 붙어 있고, `db_conn` 픽스처가 트랜잭션을 열었다가
끝나면 무조건 롤백합니다. 시드 데이터의 PK 는 실제 데이터와 겹치지 않게 90억 대역을 씁니다.

**시드 테스트가 실제 적재분과 섞입니다.** 레시피가 1,086건 들어 있는 DB 에 시드 3건을
더 넣는 구조라, `max_results` 가 작으면 시드 레시피가 순위 밖으로 밀립니다.
특정 시드 행을 찾는 테스트는 상한을 넉넉히 주세요.

`EXPLAIN` 점검은 `.env` 의 `FORBID_SEQ_SCAN_ON` 에 적은 테이블에 Seq Scan 이 걸리면
실패합니다. 단, `SEQ_SCAN_ROW_LIMIT`(기본 50,000행)보다 작게 추정되는 스캔은 넘어갑니다.
표가 작을 때는 플래너가 순차 읽기를 고르는 편이 맞고, 그걸 무조건 실패로 보면 플래너를
이기려고 SQL 을 비틀게 됩니다.

## 직접 돌려보기

```bash
uv run recsys-sql run --query product_detail --param product_id=749

uv run recsys-sql explain --query fridge_recipe_match \
    --param user_id=1 --param min_coverage=0.5 --param max_results=5
```

레포 루트에 `recsys_sql/` 디렉터리가 있어서 `python -m recsys_sql.cli` 는 그 디렉터리를
네임스페이스 패키지로 잡아 실패합니다. 위처럼 콘솔 스크립트를 쓰세요.

## 남은 일

`docs/backlog-recsys-sql.md` 를 보세요.
