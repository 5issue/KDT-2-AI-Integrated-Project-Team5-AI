# data_pipeline

출처가 제각각인 raw 데이터를 **LLM Batch API 3단계**로 판단해 Neon 테이블에 적재합니다.

raw 데이터는 CSV / PDF / 크롤링 텍스트를 겨우 parquet 으로 뽑아낸 것이라 **스키마가 통일되어
있지 않습니다.** 파이썬에 컬럼명을 적어 두는 방식으로는 새 소스가 들어올 때마다 코드를
고쳐야 하므로, 어떤 컬럼이 무슨 뜻인지부터 LLM 이 판단하게 만들었습니다.

## 3단계 구조

```
data/raw/**.parquet                     스키마 제각각 (13개 데이터셋)
  │
  │  1단계 프로파일   파일당 1콜. "이게 뭐고, 어느 테이블로 가고, 한 엔티티가 몇 행인가"
  ▼
data/artifacts/profiles.json            중간 산출물 1
  │
  │  2단계 추출       엔티티당 1콜. 프로파일대로 행을 묶어 타깃 테이블 모양으로 변환
  │                   (영어 데이터는 여기서 한국어로 정규화)
  ▼
data/artifacts/records/<dataset>.jsonl  중간 산출물 2
  │
  │  3단계 해석       재료명 -> 기존 ingredient 마스터 id
  │                   정확 일치 먼저(공짜), 남은 것만 LLM Batch
  ▼
data/artifacts/ingredient_matches.json  중간 산출물 3
  │
  │  load             COPY -> staging -> sql/ 의 upsert
  ▼
Neon: recipe / recipe_ingredient / storage_guideline
```

각 단계가 파일로 산출물을 남기므로 중간부터 다시 돌릴 수 있고, LLM 판단을 사람이 눈으로
검토할 수 있습니다. 판단 기준은 `domain.py` 의 용어 기준표와 타깃 테이블 계약이며,
세 단계 프롬프트에 모두 들어갑니다.

## 시작하기

```bash
cp data_pipeline/.env.example data_pipeline/.env   # 값은 각자 채우기
uv sync --all-packages --all-groups
uv run data-pipeline check-db
uv run data-pipeline inspect                       # raw 데이터셋 확인
uv run data-pipeline status                        # 단계별 산출물 현황
```

## 돌리는 순서

```bash
# 1단계: 프로파일 (13개 데이터셋 = 13콜, 약 32K 토큰)
uv run data-pipeline profile --job p1
uv run data-pipeline submit  --stage profile --job p1
uv run data-pipeline collect --stage profile --job p1 --wait

# 2단계: 추출 (프로파일이 loadable 로 판단한 데이터셋만)
uv run data-pipeline extract --job x1
uv run data-pipeline submit  --stage extract --job x1
uv run data-pipeline collect --stage extract --job x1 --wait

# 3단계: 재료 매칭 (정확 일치 먼저, 남은 것만 요청 생성)
uv run data-pipeline resolve --job r1
uv run data-pipeline submit  --stage resolve --job r1
uv run data-pipeline collect --stage resolve --job r1 --wait

# 적재
uv run data-pipeline load
```

Batch API 는 최대 24시간이 걸릴 수 있어 제출과 수거를 나눠 두었습니다. `--wait` 없이
`collect` 를 돌리면 상태만 확인합니다.

**처음에는 `EXTRACT_MAX_ENTITIES=5` 로 두고 2단계를 돌려 보세요.** 데이터셋당 5건만
처리하므로 프롬프트가 의도대로 동작하는지 싸게 확인할 수 있습니다.

## 1단계: 프로파일

파일마다 스키마 + 샘플 행 + **같은 폴더의 다른 데이터셋 카탈로그(이름·행수·컬럼)** 를 주고
아래를 판단하게 합니다.

- `target_tables` — 어느 테이블로 가는가 (없으면 `["none"]` + `loadable=false`)
- `rows_per_entity` / `group_by_columns` — 한 엔티티가 한 행인가, 여러 행에 흩어져 있는가
- `entity_key_columns` — 자연키가 될 컬럼
- `column_meanings` — 컬럼별 의미와 대응 타깃 필드
- `companion_datasets` — 같이 봐야 하는 다른 데이터셋
- `skip_reason` — 버전 이력, 컬럼 사전, 중복 사본 등 제외 사유

형제 데이터셋을 이름만이 아니라 **컬럼까지** 주는 이유는 중복 사본을 알아채게 하려는
것입니다. 실제 raw 에 `foodkeeper_product`(수치형)와 `foodkeeper_xls_product`(전부
문자열)가 같은 661행 데이터로 들어 있는데, 이름만 봐서는 갈리지 않습니다.

### 제약 레이어 (`stages/constraints.py`)

`collect` 는 LLM 판단을 그대로 쓰지 않고 결정적 제약을 한 번 통과시킵니다.
두 모델(`gpt-4o-mini`, `gpt-4.1-mini`)로 돌려 본 결과가 근거입니다.
**무엇이 무슨 데이터인지는 잘 판단하는데, 아래 같은 제약은 반복해서 어겼습니다.**

| 규칙 | 하는 일 | 판정 근거 |
| --- | --- | --- |
| `unknown_column` | 없는 컬럼 참조 제거 | 실제 parquet 스키마 |
| `companion` | 짝을 실측으로 교체 | 같은 이름 컬럼의 **값 겹침** |
| `storage_source` | 보관 소스 하나로 단일화 | 9개 슬롯 열거형을 값으로 가진 쪽 우선 |
| `recipe_fk` | `recipe_ingredient` 에 `recipe` 동반 | DB 외래키 |
| `companion_pair` | 검증된 짝은 반쪽이라고 버리지 않음 | 그룹 키로 실제 이어지는지 |

전부 raw 를 읽어 참·거짓을 가리므로 **데이터셋 이름을 조건에 적어 두지 않습니다.**
새 raw 가 들어와도 그대로 동작합니다. 조정 내역은 `collect` 출력에 남고, 어느 규칙이
무엇을 왜 고쳤는지 사람이 검토할 수 있습니다.

짝 판정에서 정수만 든 컬럼은 근거에서 뺍니다. 서로 다른 표의 `ID` 는 각자 1부터 세는
별개 번호라 겹침이 무조건 1.0 이 나옵니다(실제로 이 오탐을 밟았습니다).

## 2단계: 추출

프로파일의 `group_by_columns` 로 행을 묶어 엔티티 단위로 보냅니다. 파이썬은 어떤 컬럼도
이름으로 찾지 않고, 프로파일이 준 해석을 프롬프트에 그대로 실어 보냅니다.

`companion_datasets` 가 있으면 같은 엔티티의 행을 함께 넣습니다. 한국 레시피는 재료 표
(386행)와 조리 단계 표(789행)가 `recipe_name` 으로 나뉘어 있는데, 둘을 같이 보면
30개 레시피가 한 번에 정확하게 뽑힙니다.

**영어 데이터는 여기서 한국어로 정규화합니다.** 재료 마스터가 한국어(K-FIND)라 3단계
매칭이 되려면 언어를 맞춰야 합니다. 원문은 `name_original` 로 보존합니다.

## 3단계: 재료 매칭

`ingredient` 는 K-FIND 코드 체계로 큐레이션된 마스터(736행)입니다. 파이프라인은 여기에
**새 행을 만들지 않고 매칭만** 합니다.

1. 정확 일치 — `normalized_name` / `name` / `aliases` 가 그대로 맞는 것. 공짜라 먼저 씁니다.
   `normalized_name` 은 유니크가 아니므로(736행 중 18건 중복: 생강 3건 등)
   상위 항목(`parent_ingredient_id IS NULL`) 우선, 낮은 id 순으로 결정적으로 고릅니다.
2. 남은 것 — 마스터 736행을 통째로 프롬프트에 넣고 LLM 이 후보와 확신도를 고릅니다.
   `MATCH_CHUNK_SIZE` 개씩 묶어 보내 마스터 목록 토큰을 나눠 씁니다.

`MATCH_MIN_CONFIDENCE` 미만이면 채택하지 않고 미매칭으로 보고합니다. 마스터를 잘못
이어붙이느니 사람이 보고 결정하는 편이 낫다는 판단입니다.

### 알아둘 것: 마스터는 원재료 카탈로그입니다

마스터에 `배추`, `멥쌀`, `대두` 는 있어도 **`김치`, `두부`, `밥` 같은 가공식품은 없습니다**
(`is_raw_material` 기본값이 TRUE 인 이유). 레시피 재료는 가공식품이 많아 미매칭이
꽤 나올 것입니다. 리포트를 보고 셋 중 하나를 정하면 됩니다.

- 가공식품 항목을 마스터에 추가 (K-FIND 가 아닌 별도 접두사로 출처 구분)
- 가공식품 -> 원재료 매핑을 둔다 (`두부` -> `대두`)
- `aliases` 를 채워 표기 흔들림을 흡수한다 (현재 736행 전부 비어 있음)

## 적재

| 파일 | 하는 일 |
| --- | --- |
| `sql/001_staging_tables.sql` | staging 6종 + `CREATE EXTENSION vector` |
| `sql/002_insert_recipe.sql` | `(source_type, source_recipe_id)` 기준 `recipe` upsert |
| `sql/003_insert_recipe_ingredient.sql` | 매칭 결과로 FK 를 채워 `recipe_ingredient` upsert |
| `sql/004_insert_storage_guideline.sql` | `storage_guideline` upsert |
| `sql/005_insert_recipe_step.sql` | 조리 단계를 `recipe_step` 으로 upsert (줄어든 뒤쪽 단계는 삭제) |
| `sql/006_update_embedding.sql` | 임베딩 배치 결과 반영 (선택) |
| `sql/099_truncate_staging.sql` | staging 비우기 |

001~004 는 **한 트랜잭션**입니다. 004 에서 실패하면 002 가 넣은 레시피도 남지 않습니다.

> SQLAlchemy 의 asyncpg 어댑터는 SQLAlchemy 를 거친 첫 실행 전까지 트랜잭션을 시작하지
> 않습니다. `engine.begin()` 만 열고 `driver_connection` 으로 바로 내려가면 전부
> autocommit 으로 돕니다. `load_connection_scope()` 가 asyncpg 트랜잭션을 직접 열어
> 이걸 막고, `tests/test_load_transaction.py` 가 회귀를 고정합니다.

벌크 적재는 pooler 가 아니라 direct 엔드포인트(`DATABASE_URL_DIRECT`)로 붙습니다.
COPY 와 긴 트랜잭션은 PgBouncer transaction 모드와 맞지 않습니다.

`storage_guideline.storage_location` / `storage_context` 는 `source_slot` 에서
결정적으로 파생됩니다. `domain.SLOT_DERIVATION` 이 DB 의 CHECK 제약과 1:1 로 대응하는
9개짜리 조회표이고, `tests/test_domain.py` 가 어긋나지 않는지 확인합니다.
텍스트 패턴 매칭이 아니라 열거형 변환이라 파이썬에 두었습니다.

## 안전장치

- **크롤링 원문을 신뢰하지 않습니다**(OWASP LLM01). 3단계 모든 프롬프트에서 데이터를
  `<data>` 로 감싸고, 그 안의 지시문은 따르지 말라고 못박습니다.
- **structured outputs strict 모드**를 씁니다. 모델이 스키마를 벗어난 JSON 을 낼 수 없고,
  `source_slot` 같은 열거형은 DB CHECK 밖의 값을 만들 수 없습니다.
- **데이터셋 이름은 우리가 보낸 매핑이 정본**입니다. LLM 이 이름을 잘못 써도 흔들리지 않습니다.
- **원본에 없는 값은 null.** 조리 시간이나 영양정보를 지어내지 않게 프롬프트와 스키마
  양쪽에서 막습니다.
- **실패는 조용히 넘어가지 않습니다.** 거절 / HTTP 오류 / 스키마 위반 / 응답 누락을
  건수와 사유로 보고합니다.

## 마이그레이션

**마이그레이션은 이 폴더가 아니라 레포 루트의 [`database/`](../database/) 에서 관리합니다.**
data_pipeline 은 스키마를 바꾸지 않고 이미 있는 테이블에 적재만 합니다.

```bash
uv run alembic -c database/alembic.ini current
uv run alembic -c database/alembic.ini upgrade head
```

`database/migrations/env.py` 는 프로세스 환경변수 -> 루트 `.env` -> `data_pipeline/.env`
순으로 접속 정보를 찾고, `DATABASE_URL_DIRECT` 가 있으면 그쪽을 먼저 씁니다.
DDL 은 pooler 가 아니라 direct 로 거는 편이 안전하기 때문입니다.

`sql/002~005` 의 `ON CONFLICT` 는 실제 스키마에 이미 있는 유니크 제약을 씁니다
(`recipe_source_unique_idx`, `uq_storage_guideline_source_rule`). 없으면 `load` 가 먼저 막습니다.

## 테스트

```bash
uv run pytest data_pipeline/tests -q
```

| 파일 | DB 필요 | 하는 일 |
| --- | --- | --- |
| `test_domain.py` | 아니오 | 슬롯 조회표가 DB CHECK 와 일치하는지 |
| `test_schemas.py` | 아니오 | strict 스키마, 열거형 폐쇄성 |
| `test_raw_source.py` | 아니오 | 임의 컬럼 읽기, 스키마 지문 그룹핑, 마커 파일 무시 |
| `test_stage_profile.py` | 아니오 | 요청 생성, 프롬프트 내용, 결과 수거 |
| `test_stage_extract.py` | 아니오 | 엔티티 그룹핑, 동반 조인, 스키마 분기 |
| `test_stage_resolve.py` | 일부 | 청킹, 임계값, 응답 누락 처리 / 실제 마스터 정확 일치 |
| `test_bulk_insert.py` | 아니오 | staging 행 변환, 슬롯 파생, 미매칭 스킵 |
| `test_load_transaction.py` | 예 | 실제 스키마에서 001~004 실행 + 멱등성 + 롤백 |

DB 테스트는 `@pytest.mark.db` 가 붙어 있고 `DATABASE_URL` 이 비어 있으면 자동 skip 됩니다.
DB 를 쓰는 테스트도 전부 롤백되므로 데이터가 남지 않습니다.

## 이번 범위에서 뺀 것

- `foodkeeper_xls_cookingmethods`(89행) / `cookingtips`(93행) — 대응 테이블이 없습니다.
  `ai_context/todo_roadmap.md` 에 alembic 으로 테이블을 만드는 과제로 적혀 있습니다.
- `category` — 적재 대상에서 뺐습니다. FoodKeeper 분류가 영어라 신선식품 쇼핑몰
  카테고리와 성격이 다를 수 있습니다.
- `product` — 소스에 가격/SKU 가 없습니다. FoodKeeper 의 "product" 는 상점 SKU 가 아니라
  식품 항목이라 `ingredient` / `storage_guideline` 쪽으로 봤습니다.

## 스키마 후속 과제

- `storage_guideline.storage_id` 가 `BIGINT` 인데 시퀀스가 없습니다. 지금은
  `MAX + ROW_NUMBER` 로 채우는데 동시에 두 명이 적재하면 충돌합니다. `IDENTITY` 가 안전합니다.
- `ai_context/database_schema.md` 가 실제 DB 와 다릅니다. 대조표는 아래에 있습니다.

| 항목 | 문서 | 실제 DB |
| --- | --- | --- |
| `recipe_ingredient` 재료 FK | `ingredient_id2` | `ingredient_id` (+ `requirements jsonb`) |
| `ingredient.ingredient_id` | 기본값 없음 | 시퀀스 있음 |
| `ingredient.aliases`, `recipe.tags` | `TEXT` | `text[]` |
| `ingredient` 추가 컬럼 | 없음 | `source_identity_key`(UNIQUE), `parent_ingredient_id`, `metadata` |
| `recipe` 추가 컬럼 | 없음 | `source_type` + `source_recipe_id` (UNIQUE 조합) |
| `product` 추가 컬럼 | 없음 | `source_type` + `source_product_id`, `sku`/`stock_quantity` NULL 허용 |
| `recipe_product` PK | `(recipe_id, product_id)` | `(recipe_id, ingredient_id, product_id)` |
