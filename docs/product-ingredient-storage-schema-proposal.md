# Product·Ingredient·보관법 ERD 대조와 변경 제안

## 기준

이 문서는 팀 합의 기준인 `5issue AI ERD.sql`과 2026-09-09 읽기 전용 Neon 점검 결과를 함께 사용한다.

- 팀 ERD: Product·Ingredient·Storage Guideline의 논리 구조
- Neon: production과 수동 적재된 dev의 실제 물리 구조
- 이번 Alembic: 두 상태의 차이를 안전하게 맞추는 작업

팀 ERD와 Neon은 현재 완전히 같지 않다. 아래에서는 논리 ERD 변경과 이미 수동 반영된 Neon 구조를
구분한다.

## 전체 조회 경로

~~~text
User Fridge
  → Product
  → product_ingredient, role = PRIMARY
  → Ingredient
  → storage_guideline
~~~

Product는 판매 SKU, Ingredient는 식재료 기준축, Storage Guideline은 Ingredient별 공통 보관 지식이다.

## ERD와 Neon의 차이

| 대상 | 팀 ERD | 현재 Neon | 이번 migration |
| --- | --- | --- | --- |
| `ingredient` | 계층·원천 키 없음, 별칭 TEXT | Neon에는 일부 변경이 수동 반영됨 | ERD 변경과 제약 정식 반영 |
| `product` | 원천 키 없음, SKU·재고 필수 | Neon에는 일부 변경이 수동 반영됨 | 원천 키·NULL 정책·제약 정식 반영 |
| `storage_guideline` | 테이블은 이미 정의됨 | dev에는 존재, production baseline에는 없음 | production baseline에 물리 반영 |
| `user_fridge` | Ingredient와 Product를 함께 저장 | dev와 production 모두 기존 구조 | Product 기준으로 변경 |

## Ingredient

### 팀 ERD의 현재 구조

| 컬럼 | 의미 |
| --- | --- |
| `ingredient_id` | 외부 입력이 필요한 BIGINT |
| `aliases` | 단일 TEXT |
| `parent_ingredient_id` | 없음 |
| `source_identity_key` | 없음 |

### 이번 변경

다음 ERD 변경이 필요하다.

- `ingredient_id` 자동 생성
- `aliases`를 TEXT 배열로 변경
- `parent_ingredient_id` self FK 추가
- `source_identity_key` UNIQUE 추가

예를 들어 `돼지고기 → 삼겹살` 구조가 있어야 FoodKeeper의 일반 돼지고기와 특정 부위의 지침을
한 Ingredient에 섞지 않는다. 냉장·냉동 자체는 Ingredient 속성이 아니라 Storage Guideline 행의 속성이다.

## Product

### 팀 ERD의 현재 구조

| 컬럼 | 의미 |
| --- | --- |
| `sku` | 필수 |
| `storage_type` | 판매 시점의 기본 보관 장소 |
| `stock_quantity` | 필수, 기본값 0 |
| `source_type`, `source_product_id` | 없음 |

### 이번 변경

다음 ERD 변경이 필요하다.

- `source_type`, `source_product_id`와 UNIQUE 추가
- 실제 SKU가 없는 원천을 위해 `sku` NULL 허용
- 미확인 재고와 품절을 구분하도록 `stock_quantity` NULL 허용 및 기본값 제거
- `storage_type`: `냉장`, `냉동`, `상온`, NULL만 허용
- `stock_quantity`: NULL 또는 0 이상의 값만 허용

`storage_type`은 사용자의 현재 장소가 아니다. 예를 들어 냉장 삼겹살을 사용자가 냉동실로 옮겨도
Product 행은 바꾸지 않는다.

## Product Ingredient

컬럼 추가·삭제는 없다. 원물 보관법을 보여주는 대상은 아래 조건을 만족하는 Product로 한정한다.

~~~text
단일 원물 Product
AND PRIMARY Ingredient가 정확히 하나
~~~

가공식품·양념육·혼합 상품·밀키트는 구성 재료가 있더라도 원물 지침을 상품 보관법처럼 표시하지 않는다.

## Storage Guideline

### 팀 ERD의 현재 구조

`storage_guideline`은 팀 ERD와 dev Neon에 이미 존재한다. production baseline에는 아직 없으므로
`0005`는 production 기준 Alembic 경로에서 이 테이블을 물리적으로 생성한다. 수동 반영된 dev에
`0005`를 직접 실행하지 않는다.

| 필드 묶음 | 저장값 |
| --- | --- |
| Ingredient 연결 | `ingredient_id` |
| FoodKeeper 원문 | `source_item_id`, `source_food_name`, `source_food_subtitle`, `source_slot` |
| 서비스 조회값 | `storage_location`, `storage_context` |
| 기간 | `duration_min`, `duration_max`, `duration_unit`, `duration_text` |
| 설명 | `storage_tips` 전체 번역 TEXT |

### 제약

| 제약 | 이유 |
| --- | --- |
| `ingredient_id` FK | 존재하지 않는 Ingredient에 지침 연결 방지 |
| `(ingredient_id, storage_location, storage_context)` UNIQUE | 같은 서비스 조회에 서로 다른 기간이 겹치지 않도록 함 |
| `(source_item_id, source_slot)` UNIQUE | FoodKeeper 원천 slot 중복 방지 |
| 장소·상황·기간·slot CHECK | FoodKeeper slot 변환 오류 방지 |

`source_slot`은 원문 값을 보존하고, 장소와 상황은 정해진 규칙으로만 파생한다.

| source slot | 장소 | 상황 |
| --- | --- | --- |
| `pantry`, `refrigerate`, `freeze` | 상온, 냉장, 냉동 | 일반 |
| `dop_pantry`, `dop_refrigerate`, `dop_freeze` | 상온, 냉장, 냉동 | 구매후 |
| `pantry_after_opening`, `refrigerate_after_opening` | 상온, 냉장 | 개봉후 |
| `refrigerate_after_thawing` | 냉장 | 해동후 |

## User Fridge: 이번 ERD 변경 제안

### 팀 ERD의 현재 구조

| 컬럼 | 현재 의미 |
| --- | --- |
| `ingredient_id` | 보유 Ingredient FK |
| `product_id` | 구매한 Product FK |
| PK | `(ingredient_id, user_id, product_id)` |

### 제안 구조

| 구분 | 변경 | 이유 |
| --- | --- | --- |
| 삭제 | `ingredient_id` | 사용자가 보유한 단위는 재료가 아닌 실제 구매 Product |
| 추가 | `storage_location` nullable | 사용자가 상품을 실제로 둔 장소 |
| 변경 | PK를 `(user_id, product_id)`로 | 한 사용자의 한 Product 보유 행을 하나로 관리 |

### 조회 규칙

ERD에는 두 장소 컬럼을 저장하고, 어느 값을 우선할지는 서비스 조회 정책으로 처리한다.

~~~text
현재 장소 = COALESCE(user_fridge.storage_location, product.storage_type)
~~~

- `user_fridge.storage_location`이 있으면 사용자가 선택한 실제 장소를 사용한다.
- 없으면 `product.storage_type`을 상품 기본 장소로 사용한다.
- 선택된 장소에 `일반`, `구매후`, `개봉후`, `해동후`가 여러 개면 하나를 임의 선택하지 않고 함께 표시한다.

## 이번에 추가하지 않는 것

- Product별 보관법 table: 제조사·포장 기준의 공식 Product 보관 원천이 아직 없다.
- Ingredient의 냉장·냉동 컬럼: 하나의 Ingredient는 여러 장소와 상황의 보관법을 가질 수 있다.
- `ingredient_source_map`: 후보·검토 근거는 normalized CSV와 JSONL에 보존한다.
- 레시피 schema 및 레시피 적재: 이번 PR 범위 밖이다.

## 적용 전제

이 migration은 Alembic `0004_drop_non_food_category` 뒤에 실행되는 production baseline용 revision이다.
Alembic 이력이 없고 수동 schema가 섞인 `dev/subeom`에는 실행하지 않는다. 실제 production 반영 전에는
chaeyeon 님 PR에서 정한 `0001_baseline` stamp와 기존 revision 적용 절차를 먼저 검증한다.

production에서 분기한 임시 Neon 브랜치는 기본 `DATABASE_URL`을 바꾸지 않고 아래처럼 선택한다.

~~~bash
DATABASE_URL_ENV_KEY=DATABASE_URL_DEV_SUBEOM2 \
  uv run alembic -c database/alembic.ini stamp 0001_baseline
DATABASE_URL_ENV_KEY=DATABASE_URL_DEV_SUBEOM2 \
  uv run alembic -c database/alembic.ini upgrade head
~~~

`0001_baseline`은 기존 production schema를 Alembic 이력에 등록하는 빈 baseline revision이다. 새 schema를
만드는 migration이 아니며, 이번 변경은 그 뒤의 `0005`로 적용된다.
