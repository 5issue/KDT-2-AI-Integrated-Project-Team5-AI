# Storage Guideline 적재

## 왜 전수 복제가 아닌가

KIPIL의 `storage_guideline` 935행에는 서비스 자연키
`(ingredient_id, storage_location, storage_context)` 중복이 160조 있다. 전수 분해한 결과는
아래와 같다.

| 중복 160조의 내역 | 조 |
| --- | ---: |
| 기간이 같음 (표기만 다름) | 35 |
| 기간이 다름 — 원천 식품명이 서로 다름 | 82 |
| 기간이 다름 — 같은 식품명, 다른 subtitle | 43 |
| **기간이 다름 — 원천이 완전히 같음** | **0** |

같은 원천이 기간만 다르게 들어온 경우는 **0건이다.** 즉 이것은 적재 중복이 아니라 서로 다른
FoodKeeper 원천이 한 Ingredient에 뭉친 **매핑 문제**다.

```
닭고기 ← Chicken, Chicken parts, Chicken nuggets, Fried chicken, Cornish Hens, Ground turkey
돼지고기 ← Pork, Beef, Stuffed raw pork chops
전분   ← Cornmeal, Rice
호박   ← Pumpkins, Spaghetti squash
```

`닭고기`에 `Ground turkey`가, `돼지고기`에 `Beef`가 붙어 있다. 가장 짧은 기간을 고르는 식으로
자동 확정하면 생닭 상품에 튀긴 닭 지침이, 돼지고기에 소고기 다짐육 기준 `1-2일`이 사실처럼
표시된다. 모르는 값은 지어내지 않는다는 원칙에 따라 병합하지 않고 보류한다.

229개 재료 중 168개는 원천이 1종뿐이라 애초에 충돌이 없다.

## 선별 규칙

| 상태 | 처리 |
| --- | --- |
| 자연키가 유일 | 그대로 적재 |
| 여러 행이지만 기간이 하나 | 대표 1건으로 접어 적재 |
| 기간이 둘 이상 | 적재하지 않고 보류 리포트에 원천·기간·사유를 남김 |

대표는 원천 식별자 `(source_food_name, source_food_subtitle, source_slot)` 순으로 고른다.
입력 순서와 무관하게 같은 행이 뽑히므로 재적재가 멱등하다. 적재는 자연키 기준
`ON CONFLICT DO UPDATE`다.

`source_item_id`는 옮기지 않는다. staging에서만 원천 row를 식별하는 값이고 `0012`가 서비스
테이블에서 뺀 컬럼이다.

## 실행

기본은 대상 DB를 바꾸지 않는 dry-run이다.

```bash
uv run python scripts/db/load_storage_guideline.py \
  --target production --target-url-env DATABASE_URL
```

적재 전에 아래를 트랜잭션 밖에서 먼저 검사한다. 트랜잭션 중간이 아니라 시작 전에 무엇이
틀렸는지 알기 위해서다.

- 대상 DB에 `storage_guideline`이 있는지
- `0013`의 CHECK와 같은 허용값인지 (장소·상황·기간 단위)
- 모든 `ingredient_id`가 대상 DB에 있는지 (FK)
- source와 target이 다른 DB인지

실제 적용은 확인 문자열을 요구한다.

```bash
uv run python scripts/db/load_storage_guideline.py \
  --target production --target-url-env DATABASE_URL \
  --apply --confirm-production LOAD_STORAGE_GUIDELINE_V1
```

## 2026-09-21 Production 적용 결과

```
source_rows=935
accepted_rows=473  accepted_ingredients=211
held_groups=125    held_rows=402  held_ingredients=68
inserted=473 updated=0
```

`935 = 473 적재 + 402 충돌 보류 + 60 대표로 접힌 중복`.

| 확인 | 값 |
| --- | ---: |
| 적재 행수 / 재료 | 473 / 211 |
| 자연키 중복 | 0 |
| CHECK 허용값 위반 | 0 |
| 고아 FK | 0 |
| 보관법이 붙는 상품 | 0 → 1,459 (PRIMARY 관계 있는 상품 2,379 중 61%) |
| 재실행 시 | `inserted=0 updated=473`, 행수 473 유지 |

`돼지고기`는 원천에 `Beef`가 섞여 있어 규칙에 따라 보류됐다. 이번 시연 범위에서 제외한다.

## 보류 462행을 어떻게 쓰나

`data/audits/storage_guideline_holds.jsonl`에 조합별로 원천·기간·사유가 남는다. 이 파일은
버려진 데이터 목록이 아니라 **Ingredient 매핑 과제 목록**이다. 68개 재료가 대상이고, 원천을
부위·형태별 child Ingredient로 가르는 기준은
`docs/product-ingredient-storage-normalization-guide.md` 3.3절과 5.6절을 따른다.

매핑을 고친 뒤 이 스크립트를 다시 돌리면 충돌이 풀린 조합부터 자동으로 적재된다.

## 승격 스크립트와의 관계

`scripts/db/promote_kipil_catalog.py`의 현재 `TRUNCATE ... CASCADE`는 `ingredient`를 참조하는
`storage_guideline`을 **백업 없이** 함께 비운다. 이 적재 이전에는 대상이 0행이라 드러나지
않았지만, 지금은 473행이 있으므로 승격 스크립트를 먼저 고치기 전에는 실행하지 않는다.
