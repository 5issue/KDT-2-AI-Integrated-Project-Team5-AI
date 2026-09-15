# data_pipeline 남은 일

담당: 공통 (각자 돌리고 각자 스키마를 검증) · 기준일 2026-09-11

앞으로 이 폴더의 역할은 **스키마 검증과 적재**입니다. LLM 3단계는 한 바퀴 돌았고,
지금 필요한 것은 (1) 적재분의 구멍 메우기, (2) 스키마가 바뀔 때 따라가기입니다.

## 현재 적재 상태 (Neon `dev/kipil`)

| 테이블 | 행수 | 비고 |
| --- | --- | --- |
| `ingredient` | 1,028 | K-FIND 큐레이션. `ingredient_category_id` 963행 연결 |
| `recipe` | 1,086 | `cooking_method` 624행(14종), `difficulty` 25%, `description` 54% |
| `recipe_ingredient` | 8,393 | |
| `recipe_step` | 5,542 | |
| `storage_guideline` | 935 | 한국어 열거값. 중복 337행 남음 |
| `product` | 2,553 | `storage_type` 1,002행. `image_url` 컬럼 없음 |
| `product_ingredient` | 2,422 | 전부 PRIMARY. 미연결 상품 295건 |
| `category` | 220+ | |
| `recipe_product` | **0** | 큐레이션 값이라 자동 생성 안 함 |
| `product_popularity` | **0** | 주문·조회 로그 없음 |
| `user_product_affinity` | **0** | 같음 |
| `app_user` / `user_fridge` | **0** | 데모 계정 필요 |
| `embedding` 컬럼 (recipe/product/ingredient) | 4,667 / 4,667 | 2026-09-15 채움. `embed` 명령 |

alembic head: `0010_bubble_view_perf`. 누적 API 비용 약 $3.4
(임베딩 한 바퀴는 $0.002 로 무시할 수준).

---

## 1. PR #10 을 머지하려면 먼저 고쳐야 하는 것

PR #10(`0005_product_ingredient_storage_alignment`)을 라이브 DB 에 그대로 걸면
**두 군데서 실패합니다.** 롤백되므로 데이터가 깨지지는 않습니다.

### 1-1. `storage_guideline` 중복 337행

`uq_storage_guideline_query (ingredient_id, storage_location, storage_context)` 에
걸립니다. 중복 160조 / 초과 337행.

PR #11 문서 5.6절에 해소 규칙이 있습니다 — **팁이 있는 행 우선, 같으면 숫자로 정규화한
`source_item_id` 가 가장 작은 행.** 다만 두 번째 기준을 지금 쓸 수 없습니다(아래).

### 1-2. `source_item_id` 가 숫자가 아닙니다

935행 전부 `fk_134` 꼴이라 BIGINT 변환이 실패합니다. 두 갈래입니다.

- `fk_` 접두사를 떼고 숫자만 남기기 → PR #10·#11 과 맞음
- 원천 키를 문자열로 유지 → PR #10 의 그 단계를 빼야 함

접두사는 `data_pipeline` 이 붙인 것이라 떼는 쪽이 어렵지 않습니다.

### 1-3. `create_table("storage_guideline")` 이 이미 있는 표를 만듭니다

PR 쪽에서 `ALTER` 로 바꾸거나 조건부로 만들어야 합니다. PR 작성자 몫입니다.

### 1-4. 저장소가 DB 의 현재 모습을 담고 있지 않습니다

`ingredient` 에 유니크 인덱스가 둘 있는데(`ingredient_source_identity_key_unique_idx`,
`uq_ingredient_source_identity_key`) **레포 어디에도 없습니다.** `0001_baseline` 은 빈
stamp 라 alembic 밖에서 Neon 에 직접 만든 것입니다. PR 이 라이브에서 터진 진짜 이유가
이것입니다.

**`0001_baseline` 을 고쳐서 채우면 안 됩니다.** 이미 Neon 에 stamp 된 리비전이라,
거기에 DDL 을 넣으면 기존 DB 는 옛 스키마인데 새로 만드는 DB 만 그 DDL 을 돕니다.
적용된 마이그레이션은 그대로 둡니다.

대신 **앞으로 나아가는 마이그레이션**을 하나 더 만들어 맞춥니다.

- alembic 밖에서 만든 객체를 찾아, `CREATE ... IF NOT EXISTS` 로 기존 DB 에서는
  아무 일도 안 하고 새 DB 에서는 만들어지는 리비전을 추가
- 중복 인덱스처럼 정리할 것이 있으면 같은 리비전에서 `DROP INDEX IF EXISTS`
- baseline 을 새로 뜨는 것은 환경을 통째로 리셋할 때만

이걸 안 하면 앞으로도 같은 사고가 납니다.

---

## 2. 적재 구멍

| 항목 | 현황 | 필요한 것 |
| --- | --- | --- |
| `product_ingredient` 미연결 | 295건 (상품의 12%) | 상품명·카테고리 매칭으로 안 잡히는 것들. 별칭 보강 or 수기 |
| `ingredient_category_id` | 1,028 중 963 | 나머지 65행은 K-FIND 대분류가 없는 큐레이션 재료 |
| `product.image_url` | 컬럼 없음 | 크롤 원본에 있는지 확인 → 있으면 컬럼 추가 |
| `recipe.description` | 54% | 원문에 없는 경우가 많음 |
| `recipe.difficulty` | 25% | 원문에 난이도 표기가 거의 없음. 2단계 재실행해도 크게 안 오름 |
| `dish_type` | 스키마에 없음 | 버블 `혼자 먹기 딱 좋은 한 그릇` 에 필요 |

### 2단계 재실행이 필요한 것 (약 $3)

`difficulty` 와 `dish_type` 뿐입니다. 둘 다 원문 의존이라 이득이 확실하지 않습니다.
**돌리기 전에 비용 대비 효과를 먼저 재세요.**

조리법 정규화(로드맵 1번)는 재실행 없이 끝냈습니다 — 102종을 파이썬 규칙으로 14종에
모았습니다. 열거형 변환은 판단이 아니라 규칙이라 LLM 이 필요 없습니다.
**같은 성격의 일이 또 나오면 같은 방법을 먼저 검토하세요.**

---

## 3. 스키마가 바뀔 때 따라가야 하는 것

`database/` 에 마이그레이션이 들어오면 이 폴더에서 확인할 곳:

| 바뀐 것 | 볼 파일 |
| --- | --- |
| 컬럼 추가·삭제 | `sql/001_staging_tables.sql` (staging 은 `ADD COLUMN IF NOT EXISTS` 로 따라잡음) |
| 검색에 쓰는 텍스트 | `load/embedding.py` 의 `TEXT_SQL` **과** `rag_lab/.../retrieval.py` 의 `_SOURCES` (짝입니다) |
| 컬럼 추가·삭제 | `sql/00{2..8}_insert_*.sql` 의 INSERT 컬럼 목록 |
| 값 제약 변경 | `src/data_pipeline/domain.py` 의 열거 조회표 |
| 타깃 테이블 계약 | `domain.TARGET_TABLE_CONTRACTS` (1~3단계 프롬프트에 들어감) |
| 적재 튜플 순서 | `load/bulk_insert.py`, `load/catalog.py` |

`tests/test_load_transaction.py` 가 **실제 스키마에 SQL 파일을 전부 돌려 보고
롤백**합니다. 스키마가 바뀌면 여기서 먼저 걸립니다. 마이그레이션을 적용한 뒤
이 테스트부터 돌리세요.

## 4. 이번 스프린트 제외 항목이 적재에 미치는 것

확정 내용은 `ai_context/스프린트 범위와 제외 항목.md` 에 있습니다. 적재 쪽 할 일만 옮깁니다.

| 항목 | 적재 쪽 할 일 |
| --- | --- |
| 영문 레시피 전량 제외 | `recipe.source_type = 'recipes'` 1,004건. **먼저 결정 필요** — 지울지, 신규만 막을지 |
| 보관팁 시연 20종만 | 지금 229개 재료 935행이 들어가 있음. 다른 브랜치의 20종 작업과 머지 시점에 조율 |
| 갑각·패류 / 장류·향신료 | 재료를 **지우지 마세요.** `recipe_ingredient`·`product_ingredient` FK 가 걸려 있어 참조가 깨집니다. 조회 필터나 플래그로 걸러야 하는데 지금 스키마에 그 컬럼이 없습니다 |
| `MEAL_KIT` / `READY_TO_EAT` | 상품 25건(`READY_TO_EAT` 은 적재분 없음). 영향 작음 |
| 부피 -> 무게 환산, 영양성분 | 원래 범위 밖. 추가 작업 없음 |

**되돌릴 수 있습니다.** 2·3단계 산출물이 `data/artifacts/` 에 남아 있어 언제든 다시
적재하면 같은 상태가 됩니다.

## 5. 알아 둘 것

- **열거값은 한국어입니다.** `storage_type`, `storage_location`, `storage_context`,
  `duration_unit`, `cooking_method`. 화면에 그대로 나가는 값이라 저장 시점에 한 벌로
  모읍니다. `source_slot` 만 FoodKeeper 원문이라 영어입니다.
- **모르는 값은 지어내지 않습니다.** 열거값에 CHECK 가 걸린 컬럼은 비우고(`storage_type`),
  그렇지 않은 곳은 원문을 남깁니다. 어느 쪽이든 적재 보고서에 남아 눈에 띕니다.
- **LLM 은 판단, 코드는 불변식.** 1단계에서 LLM 이 제약을 다섯 번 어겨 `stages/constraints.py`
  로 옮겼습니다. 열거형 변환·중복 제거·파생 컬럼은 전부 코드 쪽입니다.
- **배치는 토큰 예산으로 쪼갭니다.** OpenAI 조직 단위 대기 토큰 한도(gpt-4.1-mini 기준
  200만)를 한 번에 넘기면 몇 초 만에 `token_limit_exceeded` 로 죽습니다.
  `BATCH_MAX_TOKENS` 기본 100만, `--parts` 로 병렬 제출.
- **`failed` 를 진행 중으로 보면 2시간을 날립니다.** 실제로 그랬습니다. `DEAD_STATUSES`
  처리가 들어가 있으니 폴링 루프를 고칠 때 빼지 마세요.
- **임베딩은 `embed` 명령이 채웁니다.** `rag_lab` 의 pgvector 검색이 여기에 의존하고,
  비어 있으면 RAG 가 통째로 멈춥니다. `--apply` 없이 돌리면 대상 수와 추정 비용만 봅니다.
  `embedding IS NULL` 인 행만 처리하므로 중간에 끊겨도 이어서 돌리면 됩니다.
- **적재는 재현 가능합니다.** Neon 브랜치를 갈아도 `data/artifacts/` 의 3단계 산출물로
  다시 적재하면 같은 상태가 됩니다.
