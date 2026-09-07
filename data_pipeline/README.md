# data_pipeline

raw 데이터를 OpenAI **Batch API** 로 파싱해서 Neon PostgreSQL 에 **bulk insert** 하는 파이프라인입니다.

Batch API 를 쓰는 이유는 두 가지입니다. 동기 호출 대비 비용이 절반이고, 수만 건을 한 번에
던져놓고 결과만 받아오면 되므로 rate limit 관리가 필요 없습니다. 대신 최대 24시간이 걸릴 수
있어서 "제출 -> 폴링 -> 수거" 를 별도 단계로 나눠 두었습니다.

## 시작하기

```bash
cp data_pipeline/.env.example data_pipeline/.env   # 값은 각자 채우기
uv sync --all-packages --all-groups
uv run data-pipeline check-db                      # Neon 연결/스키마 점검
```

`check-db` 는 서버 버전, pgvector 설치 여부, 스키마 문서 v0.1.0 의 테이블 14개 존재 여부를
확인합니다. 출력에는 자격증명과 호스트가 들어가지 않습니다.

## 5단계 흐름

```
data/raw/**/*.parquet                                원본 문서 (JSONL 도 읽힘)
  │  inspect 컬럼/행수/중복 id/본문 샘플 확인 (선택이지만 권장)
  │  build   raw -> Batch API 입력 JSONL (요청 1건 = 1줄)
  ▼
data/batch/<job>_part001_input.jsonl
  │  submit  files.create(purpose="batch") -> batches.create()
  ▼
OpenAI Batch (최대 24시간)
  │  collect batches.retrieve() 폴링 -> output/error JSONL 다운로드
  ▼
data/batch/<job>_part001_output.jsonl
  │  load    Pydantic 검증 -> COPY -> staging -> sql/ 의 upsert
  ▼
Neon: ingredient / recipe / recipe_ingredient
```

```bash
uv run data-pipeline inspect                        # data/raw 를 눈으로 확인
uv run data-pipeline build   --job recipes_20260908
uv run data-pipeline submit  --job recipes_20260908
uv run data-pipeline collect --job recipes_20260908 --wait
uv run data-pipeline load    --job recipes_20260908
```

`--raw` 를 생략하면 `data_pipeline/data/raw` 를 봅니다. 다른 경로를 쓰려면
`--raw <파일 또는 디렉터리>` 로 지정하세요.

레포 루트에 `data_pipeline/` 디렉터리가 있어서 `python -m data_pipeline.cli` 는 그 디렉터리를
네임스페이스 패키지로 잡아 실패합니다. 위처럼 콘솔 스크립트(`uv run data-pipeline`)를 쓰세요.

## raw 입력 형식

**기준 포맷은 parquet 입니다.** JSONL 도 계속 읽을 수 있습니다(`samples/` 가 JSONL 이고,
크롤링 직후 임시 확인에는 텍스트 포맷이 편해서 남겨 뒀습니다). 확장자로 자동 판별합니다.

| 확장자 | 처리 |
| --- | --- |
| `.parquet`, `.pq` | `pyarrow.dataset` 으로 배치 스트리밍 |
| `.jsonl`, `.ndjson` | 한 줄씩 읽기 |

필요한 컬럼(= JSONL 의 키)은 둘 다 같습니다.

| 컬럼 | 필수 | 설명 |
| --- | --- | --- |
| `source_id` | 예 | 원본 식별자. 배치 `custom_id` 와 `recipe.source_recipe_id` 가 됩니다 |
| `text` | 예 | 파싱할 원문 |
| `source_url` | 아니오 | 출처 링크 |

나머지 컬럼은 무시하므로 크롤러가 붙인 부가 컬럼(`crawled_at` 등)을 지우지 않아도 됩니다.

디렉터리를 넘기면 하위까지 훑습니다. 날짜별 파티션(`dt=2026-09-08/part-0.parquet`)도
그대로 읽히고, Spark 등이 남기는 `_SUCCESS` / `.crc` 마커 파일은 건너뜁니다.
한 디렉터리에 parquet 과 JSONL 이 섞여 있으면 parquet 을 먼저 읽습니다.

parquet 은 배치 단위로 스트리밍합니다. 파일이 커도 전부 메모리에 올리지 않습니다.

### 변환한 뒤에는 inspect 로 확인하세요

```bash
uv run data-pipeline inspect --raw data_pipeline/data/raw
```

```
파일     : parquet 2개
parquet 컬럼: ['source_id', 'source_url', 'text', 'crawled_at']
parquet 행수: 2
총 문서  : 2건 (고유 source_id 2개)
  [sample-001] 김치찌개  2인분 / 준비 10분 ...
```

컬럼명이 다르면 실제 컬럼 목록까지 같이 알려주고, `source_id` 가 중복이면 건수와 예시를
보여 줍니다. **Batch API 는 한 파일 안에서 `custom_id` 가 유일해야 하므로**, 중복이 있으면
`build` 가 제출 전에 막습니다. 여러 소스를 parquet 으로 합칠 때 걸리기 쉬운 지점입니다.

`text` 나 `source_id` 가 null 이거나 공백이면 조용히 빈 문서로 넘어가지 않고 실패합니다.

`data/` 는 `.gitignore` 대상이라 커밋되지 않습니다. 형식 확인용 샘플은 `samples/` 에 있습니다.

## 파싱 설계에서 지킨 것

- **결정적 값은 LLM 에게 맡기지 않습니다.** 정규화, 길이 제한, 파생 컬럼은 파이썬/SQL 에서 계산합니다.
- **structured outputs strict 모드**를 씁니다. `schemas.strict_json_schema()` 가 Pydantic 스키마를
  `additionalProperties: false` + 전체 `required` 형태로 바꿔 줍니다. 모델이 스키마를 벗어난 JSON 을
  낼 수 없으므로 후처리 파서가 필요 없습니다.
- **원문은 신뢰하지 않습니다** (OWASP LLM01). 크롤링 텍스트를 `<document>` 로 감싸고, 그 안의
  지시문은 데이터로만 취급하라고 시스템 프롬프트에 못박았습니다.
- **원문에 없는 값은 null.** 조리 시간이나 영양정보를 지어내지 않게 프롬프트와 스키마 양쪽에서 막습니다.
- **실패는 조용히 넘어가지 않습니다.** 거절/HTTP 오류/스키마 위반을 `BatchParseFailure` 로 분류해
  건수와 사유를 보고합니다.

## bulk insert 설계

행 단위 INSERT 대신 두 단계로 나눴습니다.

1. asyncpg `copy_records_to_table` 로 UNLOGGED staging 테이블에 COPY (네트워크 왕복 1회)
2. `sql/` 의 `INSERT ... SELECT` 로 staging -> 본 테이블 반영

비즈니스 규칙(중복 제거, FK 매칭, upsert)이 전부 `sql/` 안에 있어서 `recsys_sql` 과 같은 방식으로
pytest 검증을 붙일 수 있습니다.

| 파일 | 하는 일 |
| --- | --- |
| `sql/001_staging_tables.sql` | staging 5종 + `CREATE EXTENSION vector` |
| `sql/002_match_ingredient.sql` | 파싱된 재료명을 기존 `ingredient` 마스터에 **매칭만** 함. 못 찾은 것은 미매칭 표로 |
| `sql/003_insert_recipe.sql` | `(source_type, source_recipe_id)` 기준 `recipe` upsert |
| `sql/004_insert_recipe_ingredient.sql` | 매칭 결과로 FK 를 채워 `recipe_ingredient` upsert |
| `sql/005_update_embedding.sql` | `/v1/embeddings` 배치 결과를 embedding 컬럼에 반영 |
| `sql/099_truncate_staging.sql` | staging 비우기 |

001~004 는 **한 트랜잭션**에서 돕니다. 004 에서 실패하면 003 이 넣은 레시피도 남지 않습니다.

> asyncpg 를 SQLAlchemy 엔진에서 꺼내 쓸 때 주의할 점이 있습니다. SQLAlchemy 의 asyncpg
> 어댑터는 SQLAlchemy 를 거친 첫 실행 전까지 트랜잭션을 시작하지 않습니다. `engine.begin()`
> 만 열어 두고 `driver_connection` 으로 바로 내려가면 전부 autocommit 으로 돕니다.
> `load_connection_scope()` 가 asyncpg 트랜잭션을 직접 열어 이걸 막고,
> `tests/test_load_transaction.py` 가 회귀를 고정합니다.

벌크 적재는 pooler 가 아니라 **direct 엔드포인트**(`DATABASE_URL_DIRECT`)로 붙습니다. COPY 와 긴
트랜잭션은 PgBouncer transaction 모드와 맞지 않습니다.

## 재료는 매칭만 합니다

`ingredient` 는 K-FIND 코드 체계로 큐레이션된 **마스터 테이블**입니다.

```
K-FIND:01:01018:국수
K-FIND:01:01018:국수:MIDDLE:소면      <- parent_ingredient_id 로 상위와 연결
```

파이프라인은 여기에 새 행을 만들지 않습니다. 레시피 파싱 결과로 자동 등록하면 키 체계가
섞여 마스터가 지저분해지기 때문입니다. 대신 매칭만 하고, 못 찾은 재료는
`staging_unmatched_ingredient` 에 모아 적재 리포트로 보고합니다.

매칭 우선순위 (`normalized_name` 이 유니크가 아니라 결정적 규칙이 필요합니다. 736행 중
18건 중복 — `생강` 3건, `들깨`/`보구치`/`청각`/`고추냉이` 각 2건):

1. `normalized_name` 정확히 일치 > `name` 일치 > `aliases` 포함
2. 상위 항목 우선 (`parent_ingredient_id IS NULL`)
3. 그래도 같으면 낮은 `ingredient_id`

### 알아둘 것: 마스터는 원재료 카탈로그입니다

샘플 2건으로 돌려본 매칭률은 62.5% 였습니다. 마늘/대파/달걀/돼지고기/간장은 매칭되지만
**김치, 두부, 밥은 마스터에 없습니다.** 마스터에 `배추`, `멥쌀`, `대두` 는 있어도
가공식품 항목이 없기 때문입니다(`is_raw_material` 기본값이 TRUE 인 이유).

레시피 재료는 가공식품이 많으므로, 다음 중 하나를 팀에서 정해야 합니다.

- 가공식품 항목을 마스터에 추가한다 (`K-FIND` 가 아닌 별도 접두사로 출처 구분)
- 가공식품 -> 원재료 매핑을 둔다 (`두부` -> `대두`, `김치` -> `배추`)
- `aliases` 를 채워 표기 흔들림을 흡수한다 (현재 736행 전부 비어 있음)

어느 쪽이든 미매칭 리포트를 보고 결정하면 됩니다. 지금은 매칭 실패가 조용히 넘어가지 않고
건수와 원문 표기까지 보고됩니다.

## 마이그레이션

스키마는 이미 Neon 메인 브랜치에 있으므로, 기존 브랜치에는 baseline 도장만 찍고 시작합니다.

```bash
cd data_pipeline
uv run alembic stamp 0001      # 현재 스키마가 이미 반영된 상태로 표시
```

`0001` 은 아무것도 바꾸지 않는 baseline 입니다. 앞으로의 스키마 변경만 리비전으로 쌓습니다.

`sql/003~004` 의 `ON CONFLICT` 는 실제 스키마에 이미 있는 유니크 인덱스를 씁니다
(`recipe_source_unique_idx`, `uq_ingredient_source_identity_key`). 별도 마이그레이션이
필요 없고, 없으면 `load` 가 먼저 막고 안내합니다.

## 문서와 실제 스키마가 다릅니다

이 코드는 **실제 Neon 스키마**를 기준으로 작성했습니다. `ai_context/database_schema.md` 와
다른 지점이 있어서, 문서를 볼 때 참고하세요 (문서는 인간 개발자가 관리하므로 여기 적어만 둡니다).

| 항목 | 문서 | 실제 DB |
| --- | --- | --- |
| `recipe_ingredient` 재료 FK | `ingredient_id2` | `ingredient_id` (+ `requirements jsonb`) |
| `ingredient.ingredient_id` | 기본값 없음 | 시퀀스 있음 |
| `ingredient.aliases`, `recipe.tags` | `TEXT` | `text[]` |
| `ingredient` 추가 컬럼 | 없음 | `source_identity_key`(UNIQUE), `parent_ingredient_id`, `metadata` |
| `recipe` 추가 컬럼 | 없음 | `source_type` + `source_recipe_id` (UNIQUE 조합) |
| `product` 추가 컬럼 | 없음 | `source_type` + `source_product_id`, `sku` NULL 허용, `stock_quantity` NULL 허용 |
| `recipe_product` PK | `(recipe_id, product_id)` | `(recipe_id, ingredient_id, product_id)` |

실제 스키마 쪽이 더 낫습니다. 앞서 "원본 식별자 컬럼이 없다", "id 에 기본값이 없다" 로
지적했던 것들이 이미 해결되어 있습니다. 문서만 뒤처져 있습니다.

## 테스트

```bash
uv run pytest data_pipeline/tests -q
```

DB 없이 도는 테스트(스키마 strict 검증, 요청 생성, 결과 파싱, COPY 행 변환)와 `@pytest.mark.db`
가 붙은 실제 연결 테스트로 나뉩니다. 후자는 `DATABASE_URL` 이 비어 있으면 자동으로 skip 됩니다.
