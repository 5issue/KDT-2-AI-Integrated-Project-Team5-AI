# 데모 시나리오 seed

인계서 `docs/handoff/data-pipeline-demo-seed.md` 의 요구사항을 구현한다. 두 API의 데모 결과를
어느 환경에서도 다시 만든다.

```text
GET /api/v1/recommendations/my-recipes            확정 레시피가 카드에 보인다
GET /api/v1/recipes/{recipe_id}/missing-products  부족 재료와 대표 상품이 나온다
```

`seed-demo` 와는 역할이 다르다. `seed-demo` 는 무작위 데모 사용자 20명을 만들고, 이 시드는
그중 **한 사용자의 한 흐름**을 못 박는다.

## 내부 id 가 아니라 원천 키로 찾는다

내부 숫자 id 는 재적재하면 바뀐다. 대상은 설정 파일의 원천 키로 찾고, 설정에 적힌 이름과
실제 행의 이름이 다르면 그 자리에서 멈춘다. **다른 레시피에 시나리오를 덮어쓰는 것이 가장
나쁜 실패**라서, 찾았다는 것만으로는 진행하지 않는다.

| 설정 | 내용 |
| --- | --- |
| `config/recommendation_demo_recipes.csv` | 확정 레시피 6개. `(source_type, source_recipe_id)`, 기대 이름, `refresh_cycle`, `display_order`, 구매 흐름 여부 |
| `config/recommendation_demo_products.csv` | 냉장고 상품 4개와 부족 재료 대표 상품 2개. `(source_type, source_product_id)`, 기대 이름, 기대 PRIMARY 재료, 필요한 부모 재료 |

상품 설정에는 기대 PRIMARY 재료까지 적는다. 이름만 맞고 재료가 다른 행에 시나리오를 걸면
추천 결과가 조용히 달라진다.

## 무엇을 맞추나

| 대상 | 하는 일 |
| --- | --- |
| `ingredient.parent_ingredient_id` | `목심 -> 돼지고기` 처럼 설정이 요구한 부모 관계를 보장한다. **이미 다른 부모가 있으면 덮어쓰지 않고 멈춘다** |
| `product` | 데모 상품이 비활성이거나 재고 0 이면 살린다. 재고를 채우지는 않는다 — NULL 은 "수량 미상"이지 품절이 아니다 |
| `app_user` | 데모 사용자가 없으면 만든다 |
| `user_fridge` | 설정에 적힌 상품 집합으로 맞춘다. PK 가 `(ingredient_id, user_id, product_id)` 라 상품의 PRIMARY 재료를 함께 넣는다. 유효기간은 30일 뒤 |
| `recipe_product` | 부족 재료의 대표 상품에 `recommendation_priority=100` 을 자연키 upsert 한다 |

추천 SQL 은 레시피 id 를 모른다. 데모 노출 순서는 이 시드가 넣은 우선순위가 정한다.

## 실행

기본은 DB 를 바꾸지 않고 **현재 상태를 검증만** 한다. 무엇이 어긋나 있는지 먼저 보고 적용
여부를 정하라는 뜻이다.

```bash
uv run data-pipeline seed-scenario --rollback-sql backups/demo_rollback.sql
uv run data-pipeline seed-scenario --apply --rollback-sql backups/demo_rollback.sql
```

`--rollback-sql` 은 **바꾸기 전 상태**로 되돌리는 SQL 을 남긴다. 냉장고 행과 대상 레시피의
`recipe_product` 행을 그대로 적은 트랜잭션이다.

접속 정보는 `data_pipeline/.env` 에서 읽는다. 저장소 루트 `.env` 가 아니다.
`DATABASE_URL` 과 `DATABASE_URL_DIRECT` 둘 다 필요하다 — 적재 경로는 pooler 가 아니라
직결 주소를 쓴다. Neon 에서는 호스트의 `-pooler` 를 뺀 주소다. 이 값이 없으면
`data_pipeline` 의 DB 통합 테스트 6건도 같이 실패한다.

## 검증 8항목

적용 여부와 무관하게 매번 돌고, 하나라도 어긋나면 종료 코드가 1 이다.

1. 사용자와 대상 레시피가 각각 1건 존재한다.
2. 확정 레시피 6개가 원천 키로 찾히고 이름이 설정과 같다.
3. 각 노출 주기에 `display_order` 가 1, 2, 3 으로 한 번씩 있다.
4. 데모 냉장고의 활성 상품 집합이 설정과 같다.
5. 자식 재료 보유가 부모 요구에 닿는다(목심 -> 돼지고기).
6. 계층 판정 후 부족 재료가 설정의 대표 상품 재료와 같다.
7. 부족 재료마다 활성 구매 상품이 1건 이상이다.
8. 대표 상품 우선순위가 각각 100 이다.

결과에는 설정 지문, 적용 시각, 대상별 건수, 검증 결과만 남긴다. 접속 정보와 비밀번호는
출력하지 않는다.

## 2026-09-21 Production 적용 결과

적용 전 dry-run 에서 냉장고만 어긋나 있었다(토마토 `774`, 가지 `2708` 잔존).

```
설정 버전     6459a45409e4
확정 레시피   6개
냉장고        4행
계층 연결     0건   (이미 연결되어 있었음)
재고 보정     0건   (이미 활성·재고 있음)
대표 상품     2행
검증 8/8 통과
```

적용 뒤 데모 경로 실측:

| 확인 | 결과 |
| --- | --- |
| `my-recipes` 기본값(0.5 / 10) | 된장 두부찌개 `3221` **1위**, 매칭률 0.714, 카드 이미지 있음 |
| `missing-products 3221` | 고추 `3302` rank 1, 배추김치 `1547` rank 1 |
| 재실행 | 같은 결과, 검증 8/8 (멱등) |
