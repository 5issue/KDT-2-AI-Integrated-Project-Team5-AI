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
