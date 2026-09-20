# Product·Ingredient·보관법 정규화와 매핑 가이드

> 이 문서는 원본을 어떤 기준으로 분류하고 어떤 경우에 DB 관계를 만들거나 보류하는지 설명한다. Product·Ingredient·Storage Guideline schema는 PR #10의 합의·merge 이후 구조를 기준으로 한다.

## 1. 목적

서로 다른 원본은 같은 식품을 서로 다른 단위로 표현한다.

```text
K-FIND       돼지고기, 삼겹살, 원산지·등급별 관측
Kurly        국내산 냉장 삼겹살 500g
FoodKeeper   Fresh pork / Chops, 냉장·냉동 보관 기간
```

이 값을 문자열만으로 바로 연결하면 상품명, 부위, 원산지, 냉장·냉동 상태가 한 재료 개념에 섞일 수 있다.
이 가이드는 아래 서비스 관계를 만들기 위한 정규화 기준이다.

```text
Kurly Product
  → product_ingredient
  → Ingredient
  → storage_guideline
```

## 2. 공통 원칙

### 2.1 역할을 섞지 않는다

| 대상 | 질문 | 예시 |
| --- | --- | --- |
| Product | 무엇을 판매하는가 | 국내산 냉장 삼겹살 500g |
| Ingredient | 무엇인가 | 돼지고기 > 삼겹살 |
| Storage Guideline | 어떻게 보관하는가 | 냉동·구매후·4~12개월 |

냉장·냉동, 포장 중량, 원산지, 품종, 손질 여부는 일반적으로 Ingredient 자체를 새로 만드는 기준이
아니다. 단, 고기 부위나 분말처럼 구매·조리·보관에서 구별되는 식품은 별도 Ingredient가 될 수 있다.

### 2.2 RAW → Normalized → Service를 지킨다

```text
RAW
  원본 JSON, CSV, XLS
      ↓
Normalized
  원문, 정규화 문자열, 판정 근거, 보류 사유, 후보
      ↓
Service DB
  확정된 Product, Ingredient, product_ingredient, storage_guideline
```

원본에 없는 재료·중량·보관 기간은 만들지 않는다. 하나로 확정할 수 없는 항목은 service table에 억지로
넣지 않고 normalized 결과에서 `REVIEW`, `DEFER`, `OUT_OF_SCOPE` 또는 `BLOCKED` 상태로 보존한다.

### 2.3 결정적 규칙을 우선한다

```text
원천 key 재사용
→ 표준명 exact
→ 검토된 alias
→ 명시된 category 또는 child rule
→ 후보 보류
```

LLM은 이 규칙으로 후보가 좁혀졌지만 하나를 고르기 어려운 경우에만 사용한다. LLM이 새 Ingredient,
원재료 구성, SKU, 재고 수량, 보관 기간을 만들지 않는다.

## 3. K-FIND → Ingredient 정규화

### 3.1 대표식품은 기본 Ingredient다

K-FIND의 대표식품은 원칙적으로 하나의 기본 Ingredient가 된다.

```text
대표식품: 감자
관측: 감자_생것, 감자_삶은것, 감자_분말화한것

→ Ingredient: 감자
```

생것·삶은것 같은 관측 상태, 원산지, 생산 시기, 영양 관측값은 대표 Ingredient의 이름을 바꾸지 않는다.
원문 관측은 normalized layer에 남기고, 서비스 DB에는 canonical Ingredient만 적재한다.

### 3.2 중분류를 child로 올리는 경우

모든 중분류를 Ingredient로 만들지 않는다. 실제 구매·조리·검색에서 다른 재료로 다뤄질 때만 child로
승격한다. 아래는 현재 `ingredient_master_promoted_middle_foods.csv`에 명시된 **전체 중분류 승격
규칙**이다. 새 중분류를 자동으로 추가하지 않으며, 아래 설정을 검토·변경해야 한다.

| 대표 Ingredient | 승격한 중분류 Ingredient | 처리 이유 |
| --- | --- | --- |
| 국수 | 소면, 우동, 중면, 쫄면, 칼국수 | 구매·조리에서 구별되는 독립 식품 |
| 찹쌀 | 백진주 | 별도 쌀 identity |
| 당면 | 고구마 당면 | 대표식품과 중분류를 함께 써야 식품을 특정 |
| 전분 | 감자 전분, 고구마 전분, 옥수수 전분 | 대표식품과 중분류를 함께 써야 식품을 특정 |
| 상추 | 로메인 | 상추와 구별되는 구매·조리 식품 |
| 토마토 | 방울토마토 | 토마토와 구별되는 구매·조리 식품 |
| 파 | 대파 | 상품·레시피에서 별도 연결 단위 |
| 호박 | 늙은호박, 단호박, 애호박 | 호박과 구별되는 독립 식품 |
| 가자미류 | 기름가자미 | 크기별 원천 코드는 하나의 기름가자미로 병합 |
| 갈치류 | 갈치 | 크기별 원천 코드는 하나의 갈치로 병합 |
| 보구치 | 보구치 | 크기별 원천 코드는 하나의 보구치로 병합 |
| 연어류 | 연어 | 상품·레시피에서 사용하는 독립 식품 |
| 새우류 | 새우 | 상품·레시피에서 사용하는 독립 식품 |

위 표의 21개가 parent 아래로 연결되는 중분류 child다. 아래 두 항목은 K-FIND 중분류에서 왔지만
생재료의 child가 아니라 독립 Ingredient로 승격한다.

| 원천 표현 | Ingredient 구조 | 이유 |
| --- | --- | --- |
| 조미료류 마늘가루 | 마늘가루, parent 없음 | 생마늘과 별도 구매·사용 식품 |
| 조미료류 양파가루 | 양파가루, parent 없음 | 생양파와 별도 구매·사용 식품 |

고기 부위는 위 중분류 규칙과 별개다. 돼지고기·소고기·닭고기 등의 소분류에서 검토된 공통 부위만
child로 올리며, 상세 내용은 바로 다음 절에서 설명한다.

### 3.3 고기 부위는 parent-child로 관리한다

고기 전체와 부위는 같은 이름 공간에 평평하게 두지 않는다.

```text
돼지고기
├─ 삼겹살
├─ 목심
├─ 앞다리
└─ 갈비

소고기
├─ 등심
├─ 양지
└─ 갈비
```

`갈비`, `앞다리`처럼 이름이 같은 child는 parent 축종까지 포함해 구분한다. 고기 부위는 FoodKeeper의
보관 기간 차이를 안전하게 분리하기 위해 승격하지만, 원산지·등급·냉장·냉동만으로 child를 추가하지는
않는다.

```text
한돈 냉장 삼겹살 500g
→ 돼지고기 > 삼겹살

미국산 냉동 삼겹살 1kg
→ 돼지고기 > 삼겹살
```

두 Product는 다르지만 Ingredient는 같다. 원산지와 기본 보관 장소는 Product에 남는다.

### 3.4 분말은 생재료와 독립 관리한다

분말화된 재료는 단순 준비 상태가 아니라 별도 구매·사용 식품일 수 있다.

```text
마늘       parent 없음
마늘가루   parent 없음

양파       parent 없음
양파가루   parent 없음
```

따라서 레시피 또는 상품에 `마늘가루`가 있으면 생마늘로 치환하지 않는다. 새 분말 항목을 추가할 때도
자동 승격하지 않고 설정과 검토를 거친다.

### 3.5 Ingredient로 만들지 않는 값

| 표현 | 처리 위치 |
| --- | --- |
| 친환경, 국내산, 한돈, 미국산 | Product 원문 또는 origin 정보 |
| 냉장, 냉동 | Product 기본 보관 장소 |
| 500g, 1kg, 2팩 | Product 중량·수량 |
| 깐, 손질, 절단 | 원문 또는 Product 속성 |
| 생것, 삶은것 | K-FIND 관측 상태 |

이 규칙으로 `냉장 삼겹살`, `냉동 삼겹살`을 별도 Ingredient로 만들지 않는다. 둘은 같은
`돼지고기 > 삼겹살`이며 다른 보관 장소의 지침을 조회한다.

## 4. Kurly → Product 정규화

### 4.1 식품만 Product 적재 대상으로 선별한다

Kurly 원본에는 식품 외 상품도 포함될 수 있다. 식품으로 판정된 행만 Product 정규화 대상에 넣고,
비식품이나 분류가 불명확한 행은 별도 결과에 보존한다.

```text
식품 Product 후보
  감자, 생연어, 삼겹살, 우유

이번 Product 적재 제외
  주방용품, 생활용품, 식품 여부 미확인 행
```

### 4.2 상품 페이지와 Variant를 구분한다

Product는 가능하면 실제 판매 Variant 단위로 만든다.

```text
상품 페이지: 한돈 구이용 돼지고기
  ├─ Variant: 삼겹살 500g
  └─ Variant: 목심 500g
```

이 경우 두 Variant는 서로 다른 Product이며, 서로 다른 Ingredient로 연결된다.

```text
삼겹살 500g → 돼지고기 > 삼겹살
목심 500g   → 돼지고기 > 목심
```

Variant 원천 ID가 없고 옵션별 식품이 다르면 상품 페이지 이름으로 하나의 Ingredient를 확정하지 않는다.
이 경우는 `REVIEW` 또는 `UNRESOLVED_VARIANT`로 남긴다. 크롤링 파일의 행 번호로 Variant ID를
만들지 않는다.

### 4.3 Product 컬럼 정규화

| Product 정보 | 원천 예시 | 정규화 결과 |
| --- | --- | --- |
| 원천 식별 | Kurly product number 또는 Variant ID | `source_type=KURLY`, `source_product_id` |
| SKU | 판매 SKU가 있을 때 | `sku`, 없으면 NULL |
| 보관 유형 | `COLD`, `FROZEN`, `AMBIENT_TEMPERATURE` | `냉장`, `냉동`, `상온` |
| 중량 | `500g`, `1kg` | `weight_g=500`, `weight_g=1000` |
| 수량 | `2팩`, `10개입` | `unit_count=2`, `unit_count=10` |
| 재고 | 실제 수량 없음 | `stock_quantity=NULL` |
| 판매 여부 | 판매중·판매종료 원천값 | `is_active` |

`stock_quantity=NULL`은 품절이 아니라 실제 수량을 모른다는 뜻이다. 품절이 명시된 경우만 `0`으로
저장한다.

### 4.4 Product → Ingredient 매핑 범위

보관법까지 연결하는 자동 매핑은 단일 원물 Product만 대상으로 한다.

| 상태 | 예시 | 처리 |
| --- | --- | --- |
| `TARGET` | 감자, 애호박, 생닭, 삼겹살, 생연어 | Ingredient 후보 생성 |
| `REVIEW` | 혼합 채소, 원물 여부 불명확, 옵션 미분리 | 자동 확정하지 않음 |
| `DEFER` | 양념육, 훈제육, 조리 완료 식품, 밀키트 | 원물 보관법 연결 안 함 |
| `OUT_OF_SCOPE` | 비식품 또는 분류 불명확 | Product 대상에서 제외 |

냉장·냉동이라는 표현만으로 TARGET에서 제외하지 않는다. `냉동 삼겹살`도 원물 Product이므로
`돼지고기 > 삼겹살` 후보를 만들 수 있다.

### 4.5 Product → Ingredient 판정 순서

```text
1. 기존 확정 source key 매핑
2. 정규화한 상품명과 Ingredient 표준명 exact match
3. 검토된 alias match
4. 상품 category로 축종 또는 Ingredient branch 제한
5. parent와 child가 함께 후보면 더 구체적인 child 선택
6. 하나로 확정할 수 없으면 보류
```

| 상품명 | 결과 | 근거 |
| --- | --- | --- |
| 친환경 애호박 1개 | 호박 → 애호박 | 표준명 match |
| 대추방울토마토 500g | 토마토 → 방울토마토 | 검토된 판매명 표현 |
| 한돈 삼겹살 | 돼지고기 → 삼겹살 | 축종 category와 부위 child |
| 깐 감자 | 감자 | 박피는 Ingredient identity가 아님 |
| 양념 돼지불고기 | `DEFER` | 상품명만으로 구성 재료를 만들지 않음 |

확정된 단일 원물 관계만 `product_ingredient`에 `role=PRIMARY`로 적재한다. 동일 Product에 PRIMARY
Ingredient가 둘 이상이면 보관법 연결을 보류한다.

## 5. FoodKeeper → Storage Guideline 정규화

### 5.1 FoodKeeper의 매핑 단위

FoodKeeper는 하나의 품목 안에 여러 storage slot을 제공한다.

```text
FoodKeeper Item: Fresh pork / Chops
  ├─ 구매후 냉장
  ├─ 구매후 냉동
  └─ 해동후 냉장
```

매핑 단위는 slot 하나가 아니라 `source_item_id` 전체다. 하나의 FoodKeeper 품목에 속한 모든 slot은
반드시 같은 Ingredient에 연결한다.

```text
Fresh pork / Chops
  구매후 냉장 → 돼지고기 > 갈비
  구매후 냉동 → 돼지고기 > 갈비
```

냉장 slot은 parent, 냉동 slot은 child처럼 서로 다른 Ingredient에 연결하지 않는다.

### 5.2 서비스 적재 범위

이번 FoodKeeper 적재는 원물 Product 보관법 MVP다.

| FoodKeeper 유형 | 처리 |
| --- | --- |
| 생채소, 원물 과일, 생고기, 생수산물 | 매핑 후보 |
| 가공품, 혼합품, 유제품, 음료 | 현재 서비스 적재 제외 |
| 특정 부위가 명확한 육류 | 기존 child로 매핑 후보 |
| 부위·상태가 모호한 육류 | 보류 |

제외 항목은 원본을 버리는 것이 아니라 normalized 결과에 사유와 함께 남긴다.

### 5.3 FoodKeeper 품목 → Ingredient 판정

| 상태 | 조건 | 서비스 적재 |
| --- | --- | --- |
| `CHILD_EXACT` | 기존 child가 있고 source name·subtitle·축종이 하나로 특정 | 가능 |
| `PARENT_GENERIC` | 부위·상태 변형이 없는 일반 식품명 | 가능 |
| `REVIEW_REQUIRED` | child 없음, 복합 부위, 표현 모호, 기간 충돌 | 금지 |
| `OUT_OF_SCOPE` | 가공·혼합·유제품·음료 등 현재 대상 밖 | 금지 |
| `BLOCKED` | 원본 또는 연결 정보 부족 | 금지 |

예시는 다음과 같다.

```text
Pork / ribs
→ 돼지고기 > 갈비
→ CHILD_EXACT

Pork / ground
→ 분쇄육 child가 없으면 parent 돼지고기로 자동 연결하지 않음
→ REVIEW_REQUIRED

Pork / subtitle 없음
→ 일반 돼지고기 표현이고 다른 변형 조건이 없을 때만 PARENT_GENERIC 검토
```

### 5.4 source slot을 서비스 값으로 바꾼다

FoodKeeper의 원천 slot은 영어 코드로 유지하고, 장소와 상황만 고정 규칙으로 한글화한다.

| source slot | `storage_location` | `storage_context` |
| --- | --- | --- |
| `pantry` | 상온 | 일반 |
| `dop_pantry` | 상온 | 구매후 |
| `pantry_after_opening` | 상온 | 개봉후 |
| `refrigerate` | 냉장 | 일반 |
| `dop_refrigerate` | 냉장 | 구매후 |
| `refrigerate_after_opening` | 냉장 | 개봉후 |
| `refrigerate_after_thawing` | 냉장 | 해동후 |
| `freeze` | 냉동 | 일반 |
| `dop_freeze` | 냉동 | 구매후 |

LLM이 장소나 상황을 추론하지 않는다. 원천에 없는 slot은 적재하지 않고 로더와 DDL을 함께 갱신해야 한다.

### 5.5 기간과 팁

```text
FoodKeeper: 3 to 5 days
→ duration_min = 3
→ duration_max = 5
→ duration_unit = 일
→ duration_text = 3~5일
```

수치로 변환할 수 없는 `Do not freeze`, `When ripe` 같은 값은 `duration_min`, `duration_max`,
`duration_unit`을 NULL로 두고, 한글 `duration_text`에 뜻을 보존한다.

`storage_tips`는 FoodKeeper 팁 전체를 번역해 TEXT 한 셀에 저장한다. 쉼표나 마침표를 기준으로 문장을
나누지 않는다. 원문 팁은 RAW와 normalized의 원문 컬럼에 보존한다.

### 5.6 기간 충돌 처리

서비스 조회 키는 아래 세 값이다.

```text
ingredient_id + storage_location + storage_context
```

같은 조회 키에 여러 FoodKeeper 품목이 매핑될 수 있다.

```text
돼지고기 + 냉장 + 구매후
  일반 부위 → 3~5일
  분쇄육 → 1~2일
```

기간이 다르면 대표값을 만들지 않는다. 부위를 child Ingredient로 분리할 수 있는지 검토하고, 분리할 수
없으면 `REVIEW_REQUIRED`로 보류한다.

기간이 같을 때만 하나의 service row를 선택한다. 팁이 있는 행을 우선하고, 팁 유무도 같으면 숫자로
정규화한 `source_item_id`가 가장 작은 행을 선택한다. 선택되지 않은 원천 행은 normalized 결과에 남긴다.

## 6. Product에서 보관법을 조회하는 방식

### 6.1 고려한 두 가지 연결 방식

FoodKeeper는 Product SKU가 아니라 일반 식재료와 부위를 기준으로 지침을 제공한다. 따라서 이번 MVP는
`Product → Ingredient → Storage Guideline`을 사용한다.

```text
돼지고기 > 삼겹살 Ingredient
  ├─ 구매후 냉장 3~5일
  └─ 구매후 냉동 4~12개월
        ↑
국내산 냉장 삼겹살 500g Product
한돈 삼겹살 1kg Product
```

Product에 직접 지침을 복사하면 같은 원천 정보가 SKU마다 중복되고, FoodKeeper 정정·번역 변경 때
모든 Product를 갱신해야 한다. Ingredient에 한 번 연결하면 Recipe·검색도 같은 기준축을 재사용할 수 있다.

`product.storage_type`은 보관법 FK가 아니라 여러 지침 중 Product 기본 장소를 먼저 고르는 조건이다.

### 6.2 기본 Product 조회

```text
Product: 국내산 냉장 삼겹살 500g
Product.storage_type: 냉장
PRIMARY Ingredient: 돼지고기 > 삼겹살

→ 삼겹살 Ingredient의 Storage Guideline 중
   storage_location = 냉장인 지침 조회
```

개념적으로는 다음 조건이다.

```text
product_ingredient.product_id = Product.product_id
AND product_ingredient.role = PRIMARY
AND storage_guideline.ingredient_id = product_ingredient.ingredient_id
AND storage_guideline.storage_location = product.storage_type
```

같은 장소에 `일반`, `구매후`, `개봉후`, `해동후` 지침이 여러 개면 하나를 임의로 선택하지 않는다.
상황을 구분한 지침 목록으로 반환한다.

### 6.3 마이냉장고 조회

MVP에서는 `user_fridge`에 실제 보관 장소를 별도로 저장하지 않는다. 냉장고에 담긴 상품도
`product.storage_type`을 기본 장소로 사용해 보관 지침을 조회한다.

```text
냉장고 상품: 국내산 냉장 삼겹살 500g
Product.storage_type = 냉장
PRIMARY Ingredient = 돼지고기 > 삼겹살

→ 삼겹살 Ingredient의 냉장 지침 목록 표시
```

따라서 My냉장고와 Product 상세는 같은 조회 키를 사용한다.

```text
product_ingredient.role = PRIMARY
AND storage_guideline.ingredient_id = product_ingredient.ingredient_id
AND storage_guideline.storage_location = product.storage_type
```

`product.storage_type`과 `storage_guideline.storage_location`은 `0007` migration 이후
`냉장`, `냉동`, `상온`을 DB 정본 값으로 사용한다. Product의 `storage_type`이 NULL이면
장소 조건을 생략하고 PRIMARY Ingredient에 연결된 상황별 지침을 조회한다.

사용자별 실제 보관 장소 변경은 이번 MVP 범위에서 다루지 않는다.

### 6.5 보관법을 보여줄 수 있는 Product 조건

원물 보관법을 가공·혼합 상품에 그대로 적용하지 않기 위해 아래 조건을 모두 만족해야 한다.

```text
단일 원물 Product
AND
product_ingredient의 PRIMARY Ingredient가 정확히 하나
AND
해당 Ingredient에 선택한 장소의 Storage Guideline이 있음
```

아래 경우는 보관법을 단정하지 않는다.

```text
PRIMARY Ingredient가 둘 이상
가공·양념·훈제·조리 상품
혼합 상품 또는 밀키트
FoodKeeper 매핑이 REVIEW_REQUIRED 또는 OUT_OF_SCOPE
선택한 장소에 적합한 보관 지침 없음
```

이 경우 Product 원재료를 근거 없이 조합하거나, 다른 장소의 지침을 자동 대체하지 않는다.

### 6.6 Product 직접 보관법을 추가하는 시점

현재는 Product 전용 보관법 table을 만들지 않는다. 다만 아래와 같은 공식 원천이 확보되면 Product
전용 지침 또는 Ingredient 지침 override를 별도로 추가한다.

```text
제조사 포장에만 적용되는 소비기한 또는 개봉 후 기한
동일 Ingredient라도 제품별로 다른 포장 방식이나 가공 상태
상품 상세에 명시된 제조사 공식 보관 지침
```

그때의 우선순위는 다음처럼 둘 수 있다.

```text
공식 Product 전용 보관 지침
  → 있으면 우선 사용

Ingredient 공통 FoodKeeper 지침
  → Product 전용 지침이 없을 때 사용
```

하지만 현 데이터에는 Product 전용 공식 보관 원천이 없으므로, 지금 추가하면 빈 테이블과 중복 관계만
생긴다. 현재 MVP에서는 Ingredient 경유 연결이 원천의 의미와 서비스 데이터 구조에 가장 잘 맞는다.
