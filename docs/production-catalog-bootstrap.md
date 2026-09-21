# Production 카탈로그 bootstrap

## 확인된 초기 상태

2026-09-20 읽기 전용 점검에서 Production은 Product·Recipe 적재 전 상태였다.

| 항목 | Production | KIPIL |
| --- | ---: | ---: |
| Ingredient | 736 | 1,029 |
| Product | 0 | 2,554 |
| Recipe | 0 | 2,242 |
| Alembic 버전 | 없음 | 0011 |

Production에는 `recipe_step`, `storage_guideline`, `alembic_version`도 없었다. 따라서 데모
행만 먼저 넣지 않고 스키마와 기본 카탈로그를 순서대로 승격한다.

## migration 보정

PR #10 최신 head는 CI를 통과했지만 초기 Production에서 migration이 완주하지 않았다.

1. 최초 `storage_guideline` 생성 revision이 PR 병합 과정에서 삭제됐다.
2. `0007`과 `0012`는 해당 표가 이미 있다고 가정했다.
3. 기존 `0012_storage_guideline_service_key` revision ID는 Alembic 기본 32자를 초과했다.

이 브랜치는 기존 KIPIL 경로는 유지하면서 초기 DB에서는 후속 migration이 안전하게 건너뛰고
`0013_storage_bootstrap`에서 최종 표를 생성하도록 보정한다.

Production과 같은 2026-09-18 `default.full.sql`을 로컬 PostgreSQL에 복원한 뒤 아래 흐름이
`0013_storage_bootstrap (head)`까지 완료되는 것을 확인한다.

```bash
uv run alembic -c database/alembic.ini stamp 0001_baseline
uv run alembic -c database/alembic.ini upgrade head
```

## 사라진 정합화 migration과 0014

`0001_baseline`은 빈 revision이다. 저장소에는 base schema를 만드는 DDL이 없고, 손으로 만든
Production의 표 15개가 시작점이라고 선언만 한다. 그래서 alembic 이력은 실제 DB보다 항상 덜
말한다. 아래 문제도 여기서 나왔다.

PR #10의 `0005_product_ingredient_storage_alignment`는 번호 재배치 과정에서 파일이 옮겨지지
않고 `0012_storage_guideline_service_key`로 대체됐다. `0012`에는 Storage Guideline의 서비스
자연키 투영만 남았고 나머지는 사라졌다.

| `0005`가 하던 일 | `0012`에 남았나 | 복구 |
| --- | :---: | --- |
| `create_table(storage_guideline)` | X | `0013` |
| ingredient `source_identity_key` UNIQUE, `parent_ingredient_id` self FK | X | `0014` |
| product `(source_type, source_product_id)` UNIQUE, 재고 CHECK | X | `0014` |
| product `sku` NULL 허용 | — | 이미 반영됨 |
| product `storage_type` CHECK | — | `0007`이 만듦 |
| user_fridge `ingredient_id` 삭제, PK 변경 | X | **미착수** |
| Storage Guideline 서비스 자연키 투영 | O | — |

컬럼은 손으로 먼저 들어가 있어 조회는 됐지만 제약이 없었다. baseline이 갖고 있던 상품 식별
장치는 `uq_product_sku` 하나인데, `sku`가 NULL인 상품이 2,554개 중 1,111개라 절반 가까이
아무 유일성도 없었다. `source_identity_key`는 NOT NULL인데 UNIQUE가 없어 이름과 달리 정체성을
보장하지 않았다.

`0014`는 제약을 걸기 전에 위반 건수를 세고 멈춘다. 원본의 중복 검사는 `GROUP BY`가 NULL을 한
묶음으로 세어 NULL이 둘만 있어도 걸렸는데, UNIQUE는 NULL을 여러 개 허용하므로 검사에서 뺐다.

`user_fridge`는 이 migration에 넣지 않는다. `ingredient_id`를 읽는 쿼리는 없지만 쓰는 쪽이
`serving`, `data_pipeline`, `recsys_sql` 테스트에 걸쳐 있고 API의 품목 식별자와도 얽힌다.
별도 PR로 다룬다.

## 2026-09-21 적용 결과

```
0013_storage_bootstrap -> 0014_alignment_constraints -> 0015_product_image_url (head)
```

| 확인 | 결과 |
| --- | --- |
| `uq_ingredient_source_identity` / `fk_ingredient_parent` / `idx_ingredient_parent` | 생성됨 |
| `uq_product_source` / `ck_product_stock_nonnegative` | 생성됨 |
| `product.image_url` | 추가됨 |
| 없는 부모를 가리키기 | ForeignKeyViolation으로 차단 |
| 음수 재고 | CheckViolation으로 차단 |
| `source_identity_key` 중복 | UniqueViolation으로 차단 |
| 자식을 부모보다 먼저 INSERT (평소) | ForeignKeyViolation으로 차단 |
| 자식을 부모보다 먼저 INSERT (`SET CONSTRAINTS ALL DEFERRED`) | 커밋 시점 검사로 통과 |

적용 전 위반은 양쪽 DB 모두 0건이었다. 차단 확인은 트랜잭션 안에서 시도하고 롤백했다.

`fk_ingredient_parent`는 처음에 NOT DEFERRABLE로 걸었다가 같은 날 `downgrade 0013` →
`upgrade head`로 DEFERRABLE INITIALLY IMMEDIATE로 다시 걸었다. 카탈로그 승격의 COPY는 물리
순서로 나가서 자식이 부모보다 먼저 올 수 있는데, 즉시 검사면 그 자리에서 죽는다. 지금은 22개
자식 행 전부 부모 뒤에 있어 우연히 통과하지만 UPDATE 한 번이면 순서가 바뀐다. 승격 스크립트는
복원 트랜잭션 안에서 `SET CONSTRAINTS ALL DEFERRED`를 건다.

## 되감을 때

`upgrade`는 표가 있으면 건너뛰는데 `downgrade`가 있으면 지우면, 만든 적 없는 revision이 남의
표를 삭제한다. KIPIL에서는 기존 `storage_guideline`과 그 데이터가 사라진다. 그래서 `0013`은
표를 만들 때 표식을 남기고, 되감을 때 그 표식이 있는 경우에만 지운다.

```sql
COMMENT ON TABLE storage_guideline IS 'created_by:0013_storage_bootstrap';
```

`0007`의 `downgrade`도 같은 조건으로 나눈다. 초기 Production 경로에서는 `0013`이 표를 먼저
지우므로 없는 표에 접근하게 되고, 그대로 두면 undefined-table 오류로 멈춘다. `product`는 두
경로 모두 존재하므로 조건 밖에 둔다. `0012`는 이미 같은 guard를 갖고 있다.

| DB | `storage_guideline` 표식 | `0013` downgrade | 이후 |
| --- | --- | --- | --- |
| KIPIL | 없음 (먼저 있던 표) | 지우지 않음 | `0012`, `0007`이 차례로 되돌림 |
| Production | 없음 (`0013` 적용 시점에 표식 기능이 없었음) | 지우지 않음 | 같음 |
| 새 초기 DB | 있음 | 지움 | `0012`, `0007`은 표가 없으므로 건너뜀 |

Production의 표는 `0013`이 만들었지만 표식이 없다. 지금은 보관 지침 473행이 들어 있으므로
표식을 나중에 달지 않는다. 표식이 없는 쪽이 데이터를 남기는 동작이고, `0012`와 `0007`이
schema를 차례로 되돌리므로 이력과 schema도 어긋나지 않는다.

## 카탈로그 승격

기본 실행은 읽기 전용 건수 비교다.

```bash
uv run python scripts/db/promote_kipil_catalog.py \
  --target production \
  --target-url-env DATABASE_URL
```

실제 적용은 다음 조건을 모두 검사한다.

- source와 target이 다른 DB
- 양쪽에 migration 완료 표가 존재
- Production 주문 및 주문 항목 0건
- Docker PostgreSQL 18 클라이언트 사용 가능
- Production 확인 문자열 일치

적용 명령은 다음과 같다.

```bash
uv run python scripts/db/promote_kipil_catalog.py \
  --target production \
  --target-url-env DATABASE_URL \
  --apply \
  --confirm-production PROMOTE_KIPIL_CATALOG_V1
```

적용은 하나의 DB 트랜잭션에서 기존 핵심 카탈로그를 백업 스키마로 복사한 뒤 KIPIL 데이터로
교체한다. 실패하면 truncate와 restore를 모두 롤백한다. 접속 비밀번호는 명령 인자나 출력에
포함하지 않는다.

## 무엇을 복제하고 무엇을 비우는가

| 구분 | 표 | 백업 | 복제 |
| --- | --- | :---: | :---: |
| 카탈로그 | Category, Ingredient, Product, Product Ingredient, Recipe, Recipe Ingredient, Recipe Step, Recipe Product, Product Popularity, App User, User Fridge, User Product Affinity | O | O |
| 참조 표 | Storage Guideline, Order Item, Order Header | O | X |

참조 표는 카탈로그를 FK로 참조하므로 카탈로그를 비우려면 함께 비워야 한다. 복제하지는 않는다.

이전 구현은 `TRUNCATE ... CASCADE`로 이 세 표를 **백업 없이** 비웠다. 대상이 0행이던 동안에는
드러나지 않았지만, Storage Guideline이 적재된 뒤에는 그대로 사라지는 경로였다. 지금은
`CASCADE`를 쓰지 않고 이름으로 열거하며, 셋 다 백업 스키마에 들어간다. 카탈로그를 참조하는
표가 새로 생기면 `verify_reference_closure`가 이름을 알려 주고 중단한다.

주문 표는 복구할 원천이 없으므로 비어 있지 않으면 중단한다. 검사는 표를 잠근 뒤 트랜잭션
안에서 다시 한다. 잠그기 전의 검사만으로는 검사와 삭제 사이에 들어온 주문을 못 본다.

**적용 후 Storage Guideline은 비어 있다.** `scripts/db/load_storage_guideline.py`를 다시
실행해 채운다. 실행 절차와 선별 규칙은 [Storage Guideline 적재](storage-guideline-load.md)를
따른다.

## 적용 후 확인

1. 출력된 source와 target 표별 행 수가 같은지 확인한다.
2. `my-recipes`를 데모 사용자 `9200000002`로 조회한다.
3. Recipe `3221`의 `missing-products`를 조회한다.
4. 부족 재료가 고추·배추김치인지 확인한다.
5. 고추 상품 `3302`, 배추김치 상품 `1547`이 각각 1순위인지 확인한다.
6. 출력된 `kipil_catalog_backup_*` 스키마를 배포 기록에 보관한다.

## 2026-09-20 진행 상태

- Production 2026-09-18 스냅샷을 로컬에 복원해 `0001 → 0013` migration을 검증했다.
- 현재 KIPIL 12개 핵심 표를 로컬 Production 복제본으로 두 번 연속 승격했다.
- 두 실행 모두 표별 행 수가 KIPIL과 같았고 `missing-products`가 고추·배추김치 상품을
  기대 순위로 반환했다.
- 실제 Production에 migration `0013_storage_bootstrap`과 KIPIL 카탈로그를 적용했다.
- 적용 전 전체 대상 표는 `pre_catalog_bootstrap_20260920084324`에 백업했다.
- 카탈로그 교체 직전 대상 표는 `kipil_catalog_backup_20260920084430`에 다시 백업했다.
- 적용 후 Ingredient 1,029건, Product 2,554건, Recipe 2,242건,
  Product Ingredient 2,415건, Recipe Ingredient 18,947건, Recipe Step 12,259건을 확인했다.
- 데모 사용자 `9200000002`와 레시피 `3221`이 존재하고, 계층 판정 SQL에서 고추 상품
  `3302`와 배추김치 상품 `1547`이 각각 1순위로 반환되는 것을 확인했다.

Production이 이미 `0013_storage_bootstrap`을 기록하고 있으므로 이 PR을 병합해 저장소의
migration 이력을 실제 DB와 일치시켜야 한다. 병합 전까지 Production에서 Alembic upgrade나
downgrade를 실행하지 않는다.

참고로 KIPIL의 기존 Storage Guideline 935행에는 서비스 자연키 중복이 있어 0012 migration이
의도대로 중단된다. 이번 카탈로그 복제에서는 Storage Guideline을 제외했으며,
`missing-products` 경로에는 영향이 없다. 이 중복 정리는 별도 데이터 정합성 Task로 남긴다.
