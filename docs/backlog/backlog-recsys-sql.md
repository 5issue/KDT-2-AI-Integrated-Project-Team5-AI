# recsys_sql 남은 일

담당: openLeeWorld · 기준일 2026-09-11

최종 목표는 **`serving` 의 repository layer** 입니다. API 를 호출하면 관련 SQL 이
그대로 돌고, 폴더마다 다른 것은 `.env` 뿐인 상태.

연결 구조는 끝났습니다(`recsys_sql.repository`). 남은 것은 카탈로그를 채우고
받쳐 주는 데이터를 만드는 일입니다.

---

## 1. 스키마가 없어서 못 하는 것 (먼저 결정 필요)

### 1-1. `product.image_url` 이 없습니다

`api_spec.md` 14·15·20절이 상품 이미지를 요구하는데 컬럼도, 크롤 metadata 도 없습니다
(`metadata` 에는 `brand`, `crawled_at`, `source_record_type`, `source_url` 뿐).
`product_detail` 응답에서 뺐습니다.

- 원본 크롤에 이미지 주소가 있는지 확인
- 있으면 `database/` 에 컬럼 추가 + `data_pipeline/load/catalog.py` 에서 채우기
- 없으면 FE 와 합의해서 응답 규격에서 빼기

`recipe.image_url` 은 있습니다(1,086 중 1,004행).

### 1-2. `user_fridge` 에 단일 식별자가 없습니다

`api_spec.md` 20·23절의 `fridge_item_id` 가 스키마에 없습니다. PK 가
(user_id, product_id) 복합키입니다. 지금은 `my_fridge_items` 가 `product_id` 를
수정·삭제 키로 내보냅니다.

같은 상품을 유통기한이 다르게 두 번 넣을 수 있어야 한다면 대리키가 필요합니다.
PR #10 이 PK 를 건드리므로 그것과 함께 정하는 편이 낫습니다.

### 1-3. `cooking_method` 를 spec 은 영어로 적었습니다

`api_spec.md` 16·17절이 `"BOIL"` 입니다. 지금 DB 는 `끓이기` 입니다(보관 열거값을
한국어로 통일한 결정과 맞춘 것). FE 와 한쪽으로 정해야 합니다.

---

## 2. 데이터가 없어서 못 하는 것

### 2-1. `product_popularity` / `user_product_affinity` 가 0행입니다

주문·조회 로그가 없어 인기도 점수를 만들 원천이 없습니다. 그래서
`bubble_products` 는 **버블 안에서의 재료 쓰임새**(후보 레시피 중 몇 개가 이 재료를
쓰는가)로 정렬합니다.

로그가 생기면 `ai_context/data_erd_sql_design.md` 16절의 가중 합으로 바꿉니다.
신규 사용자는 인기도 0.8 / 이력 0.2, 충성 고객은 반대.

`reorder_candidates` 는 `user_product_affinity` 없이는 아예 빈 결과입니다.

### 2-2. `recipe_product` 가 0행입니다

"이 레시피의 이 재료 자리에는 이 상품" 이라는 **큐레이션** 값이라 자동으로 채우면
의미가 없어집니다. 그래서 `product_recipes` 는 ERD 18절(재료 경유)을 본선으로 쓰고,
`recipe_product` 는 채워지면 우선순위가 먼저 오도록 LEFT JOIN 만 걸어 뒀습니다.

기획에서 "이 레시피엔 이 상품을 밀자" 가 정해지면 그때 몇 건만 넣으면 됩니다.

### 2-3. `app_user` / `user_fridge` 가 0행입니다

마이냉장고 쿼리 4종은 시드 트랜잭션 위에서만 검증됩니다. 데모 계정 몇 개를
넣어 두면 실제 화면을 확인할 수 있습니다.

---

## 3. 카탈로그에 아직 없는 것

| 필요한 곳 | 비고 |
| --- | --- |
| `GET /recipes/{id}/missing-products` | `_template/missing_ingredient_products` 를 openLeeWorld 폴더로 옮기고 응답 모양을 api_spec 에 맞추기 |
| 냉장고 CRUD (POST/PATCH/DELETE) | 쓰기라 카탈로그(읽기 전용) 밖. 서빙이 직접 쓰거나, 쓰기 카탈로그를 따로 두는 설계가 필요 |
| 검색 | api_spec 에 없지만 프로젝트 범위에 있음. `rag_lab` 과 겹치는 영역 |

## 4. 버블

### 영문 레시피를 빼도 이제 4개는 뜹니다 (COOKRCP01 적재 후)

디자인팀이 이번 스프린트에서 영문 레시피를 전량 제외하기로 했습니다.

**"제외" 는 삭제가 아닙니다.** 이미 적재된 1,004건은 **보존**하고, **신규 적재만** 한국어 raw 로 제한합니다
(`docs/backlog-data-pipeline.md` 4절과 같은 정책입니다). 그래서 아래 "전체" 열에는
영문 1,004건이 아직 들어 있고, "한국어만" 열이 **영문을 화면에서 빼기로 결정했을 때**의
후보 수입니다.

**아래는 `recipe` 전체 2,242건 기준입니다.** 구성은 COOKRCP01 1,156 / 영문 1,004 /
테스트 52 / 기존 한국어 30 이고, "한국어만" 열은 `source_type` 이
`MFDS_COOKRCP01` 또는 `korean_recipe_steps` 인 것만 센 값입니다.

| 버블 | 전체 | 한국어만 | `min_candidates=10` |
| --- | --- | --- | --- |
| `FEW_STEPS` (손 덜 가는) | 901 | 107 | OK |
| `LIGHT` (채소·과일) | 748 | 491 | OK |
| `LOW_INGREDIENT` (재료 적게) | 1,165 | 494 | OK |
| `MEAT` (고기 든든) | 367 | 223 | OK |
| **`QUICK_15MIN` (15분 안에)** | 233 | **0** | **미달** |

> 이 표는 **COOKRCP01 1,156건을 적재한 뒤** 다시 잰 값입니다. 적재 전에는 한국어가
> 30건뿐이라 `MEAT` 하나만 간신히 걸렸고(15건), 나머지 넷이 전부 0~8 이었습니다.

남은 하나는 `cook_time_min` 때문입니다. COOKRCP01 에 조리시간 필드가 없어 한국어
원천의 `cook_time_min` 이 여전히 0건이고, **`QUICK_15MIN` 만 구조적으로 안 걸립니다.**

선택지와 판단 근거는 `ai_context/스프린트 범위와 제외 항목.md` 3절에 있습니다.
"이미 적재된 것은 두고 신규 적재만 중단" 으로 가면 코드를 손대지 않아도 됩니다.
규칙을 다시 짜야 한다면 `alembic 0006`(시드)과 `0008`/`0010`(뷰)을 함께 고칩니다.

### 리롤 정책이 아직 없습니다

`ai_context/erd_추가내용.md` 가 요구하는 것 중 구현이 없는 부분입니다.

| 요구 | 상태 |
| --- | --- |
| `min_candidates` 게이트 | 있음 (`bubble_candidate_counts.is_servable`) |
| 직전 노출 세트 제외 (2회차까지) | **없음** |
| 세션 단위 시드 고정 | **없음** |
| 랜덤 5개 샘플링 | **없음** |
| 사용자별 독립 리롤 커서 | **없음** |

세션 상태는 서빙 몫입니다. 카탈로그는 후보 풀까지 책임지고, 직전 세트 제외는
`exclude_keyword_ids` 같은 파라미터로 받는 편이 맞습니다.

같은 문서가 `bubble_keyword_recipe` 를 **야간 배치로 사전 계산**하자고 합니다.
지금은 뷰(`0008`/`0010`)라 실시간 조인인데, 최적화 후 26ms / 416 블록이라 문서가 말한
"수십 ms" 는 이미 충족합니다. 배치 표는 갱신 지연이 생기므로 현재 규모에서는 뷰가
낫다고 봅니다. 데이터가 수만 건이 되면 뷰를 `CREATE TABLE AS` 로 물질화하면 됩니다.
**어느 쪽으로 갈지 결정이 필요합니다.**

### 나머지

- **`전자레인지·에어프라이어 간편식`** — 조리법 정규화가 끝나 이제 가능합니다.
  다만 후보가 8+2=10건으로 `min_candidates` 에 딱 걸칩니다. 데이터가 조금만 줄면
  화면에서 내려갑니다. 권하지 않습니다.
- **`요리 초보도 실패 없는`** — `difficulty` 가 25%만 채워져 보류. 지금은 단계 수 기반
  `FEW_STEPS` 로 대체하고 있습니다.
- **`혼자 먹기 딱 좋은 한 그릇`** — `dish_type` 이 스키마에 없어 보류.
- 새 `rule_type` 을 쓰려면 **`bubble_recipe_candidate` 뷰(alembic 0008)를 고쳐야
  합니다.** `0005` 의 CHECK 에는 `seasonal`, `popularity`, `text_search` 도 있지만
  해석이 없습니다. 해석 없는 rule_type 은 후보 0건이 되어 `min_candidates` 에서 걸립니다.

## 5. 적재 쪽에 넘기는 것

**`storage_guideline` 중복 337행 중 125조는 기간이 어긋납니다.** `게류 냉장 구매후` 에
`10-12개월` 과 `2-4 일` 이 함께 있습니다.

`product_storage_guideline` 이 조회 시점에 짧은 쪽을 고르도록 막아 두었지만, 그건
방어일 뿐입니다. 원천에서 정리해야 합니다 — 규칙은
`docs/product-ingredient-storage-normalization-guide.md` 5.6 절에 있고,
`docs/backlog-data-pipeline.md` 1-1 과 같은 항목입니다.

## 6. 운영

- **CI 에서 DB 테스트가 돕니다.** 지금은 `.env` 가 없어 44개가 조용히 skip 됩니다.
  GitHub Actions 에 `DATABASE_URL` 시크릿을 넣고 DB 잡을 따로 두는 인프라 변경이라
  합의가 필요합니다.
- **skip 은 조용합니다.** `.env` 를 안 채운 채 "통과" 를 보면 DB 쿼리가 한 줄도 안 돈
  것일 수 있습니다. 실제로 버블 테스트 5개가 그렇게 죽어 있었습니다
  (픽스처 이름 오타 + `as_dicts()` 오용). `-rs` 로 skip 사유를 확인하세요.
- **`FORBID_SEQ_SCAN_ON`** — `SEQ_SCAN_ROW_LIMIT`(기본 5만행)를 넘을 때만 실패합니다.
  데이터가 커지면 이 값을 낮춰 실제 규모에 맞추세요.
