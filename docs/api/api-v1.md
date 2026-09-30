# AI 파트 API v1 명세

FE/BE 통합 인터페이스 명세(노션 v0.2)에서 AI 파트 몫을 옮겨 온 정본입니다.
노션 문서와 어긋나면 이 파일 기준으로 맞추고, 계약 변경은 PR 리뷰로 합의합니다.

## 공통 응답 envelope

백엔드(Spring) `ApiResponse` 와 동일한 5필드를 사용합니다. 구현은
`serving/src/serving/envelope.py` 입니다.

```json
{
  "status": "SUCCESS",
  "message": "요청에 성공하였습니다.",
  "data": {},
  "error": null,
  "timestamp": "2026-09-16T09:00:00Z"
}
```

- `status`: `SUCCESS` | `ERROR`
- `error`: 에러 코드 enum 이름 문자열 (예: `"RESOURCE_NOT_FOUND"`). 성공 시 `null`
- invariant: `status=SUCCESS` 이면 `error=null`. 단 성공이어도 본문이 없으면
  (DELETE 등) `data=null` 일 수 있습니다
- `timestamp`: 초 단위 UTC (`yyyy-MM-dd'T'HH:mm:ss'Z'`), 백엔드 `@JsonFormat` 과 동일
- 금액 등 Decimal 필드는 JSON number 로 직렬화합니다
- 목록 API 의 pagination(`next_cursor`/`has_next`)은 제외로 확정되었습니다
- 헬스체크(`/health`, `/health/db`)는 envelope 적용 대상에서 제외하며
  HTTP status code 로만 판단합니다

## 에러 코드

백엔드 `GlobalErrorCode` 를 미러하고 AI 파트 확장 2종을 더합니다.
구현은 `envelope.ErrorCode`, 변환은 `exceptions.py` 의 전역 핸들러가 합니다.

| HTTP | error | 발생 조건 |
| --- | --- | --- |
| 400 | `INVALID_INPUT_VALUE` | 잘못된 요청 |
| 401 | `UNAUTHORIZED` | 사용자 식별 실패 |
| 403 | `FORBIDDEN` | 권한 없음 |
| 404 | `RESOURCE_NOT_FOUND` | 리소스 없음. 타인 소유 리소스도 구분 없이 404 |
| 405 | `METHOD_NOT_ALLOWED` | 지원하지 않는 메서드 |
| 409 | `CONFLICT` | 상태 충돌 |
| 422 | `INVALID_INPUT_VALUE` | 검증 실패 (새 코드 대신 재사용) |
| 429 | `TOO_MANY_REQUESTS` | rate limit 초과: 기본 60/분, `my-recipes`(LLM 경로) 10/분, `Retry-After` 헤더 포함 (AI 파트 확장) |
| 500 | `INTERNAL_SERVER_ERROR` | 처리되지 않은 예외. 상세는 응답에 싣지 않음 |
| 503 | `SERVICE_UNAVAILABLE` | DB 미연결 등 (AI 파트 확장) |

## 인증

BE 보안 정책(2026-09-29 BE 답변)에 맞춰 **Bearer JWT** 로 사용자를 식별합니다. 동기 호출은 JWT
원문을 전파하고, 서빙은 auth 서버의 `GET /.well-known/jwks.json` 공개키로 서명을 검증한 뒤
`sub`(user_db `users.id`)를 사용자 id 로 씁니다. 구현은 `serving/src/serving/auth.py` 한 곳이고,
설정과 BE 에 받아야 하는 값은 `docs/api/be-sync.md` 2절에 있습니다.
경로에 user id 를 받는 방식은 IDOR 위험이 있어 쓰지 않습니다.

- 요청 헤더: `Authorization: Bearer <JWT>`.
- 필수: `RECO-02`, `FRIDGE-01~04`, `FAV/RECENT`. 토큰이 없거나 서명·만료·발급자 검증에 실패하면
  401 `UNAUTHORIZED` 입니다(사유는 로그에만). JWKS 를 받지 못하면 503 `SERVICE_UNAVAILABLE`.
- 선택: `RECIPE-03`. 토큰이 있으면 냉장고를 반영하고, 없으면 익명으로 계산합니다. 있는데
  틀리면 401 입니다.
- 나머지(`HOME-01`, `RECO-01`, `PROD-*`, `RECIPE-01`)는 사용자 맥락을 쓰지 않습니다.
- 토큰의 `sub` 는 rate limit 의 사용자 키이기도 합니다. 로그인 사용자는 공개 API 에도 실어
  보내면 사용자별로 셉니다. 비로그인 요청은 BFF 가 `X-Forwarded-For` 로 원 사용자 IP 를
  넘겨야 사용자별로 셉니다(넘기지 않으면 BFF IP 하나로 합산).
- **로컬 전용 대안**: `JWT_JWKS_URL` 이 비어 있으면 예전 방식대로 `X-User-Id` 헤더(양의 정수)를
  받습니다. JWT 가 켜지면 이 헤더는 무시합니다. 배포 환경은 항상 JWT 를 켭니다.

## 엔드포인트

Base URL: `/api/v1`

| Index | 기능 | 메서드 | 경로 | 상태 |
| --- | --- | --- | --- | --- |
| HOME-01 | 홈 버블 목록 | GET | `/home/bubbles` | 구현됨 |
| RECO-01 | 버블 기반 상품 추천 | GET | `/recommendations/products` | 구현됨 |
| RECO-02 | My냉장고 기반 레시피 추천 | GET | `/recommendations/my-recipes` | 구현됨 |
| PROD-01 | 상품 상세 | GET | `/products/{product_id}` | 구현됨 |
| PROD-02 | 상품으로 만들 수 있는 레시피 | GET | `/products/{product_id}/recipes` | 구현됨 |
| PROD-03 | 상품 보관 가이드 | GET | `/products/{product_id}/storage-guide` | 구현됨 (명세 조정 필요) |
| RECIPE-01 | 레시피 상세 | GET | `/recipes/{recipe_id}` | 구현됨 (명세 조정 필요) |
| RECIPE-03 | 부족 재료 상품 추천 | GET | `/recipes/{recipe_id}/missing-products` | 구현됨 (18장 통합) |
| FRIDGE-01 | My냉장고 품목 목록 | GET | `/users/me/fridge` | 구현됨 |
| FRIDGE-02 | My냉장고 품목 추가 | POST | `/users/me/fridge` | 구현됨 |
| FRIDGE-03 | My냉장고 품목 수정 | PATCH | `/users/me/fridge/{product_id}` | 구현됨 (키 변경) |
| FRIDGE-04 | My냉장고 품목 삭제 | DELETE | `/users/me/fridge/{product_id}` | 구현됨 (키 변경) |
| FAV-01 | 찜한 레시피 목록 | GET | `/users/me/favorite-recipes` | 구현됨 |
| FAV-02 | 레시피 찜 추가 | POST | `/users/me/favorite-recipes/{recipe_id}` | 구현됨 |
| FAV-03 | 레시피 찜 취소 | DELETE | `/users/me/favorite-recipes/{recipe_id}` | 구현됨 |
| RECENT-01 | 최근 본 레시피 목록 | GET | `/users/me/recent-recipes` | 구현됨 |
| RECENT-02 | 레시피 조회 기록 | POST | `/users/me/recent-recipes/{recipe_id}` | 구현됨 |
| RECENT-03 | 최근 본 레시피 선택 삭제 | DELETE | `/users/me/recent-recipes` | 구현됨 |

- `RECIPE-03` 은 18장(missing-ingredients)과 통일한 단일 API 입니다 (팀 합의).
  부족 재료 목록과 재료별 추천 상품을 한 번에 냅니다. 토큰이 없으면(비로그인)
  냉장고 갈래 없이, `base_product_id=0` 이면 기준 상품 갈래 없이 계산합니다.
  살 수 있는 상품이 없는 재료는 목록에 내지 않습니다(기획 합의 — 품절 표시는 일반
  검색 몫). 상품 `image_url` 은 product 컬럼 migration 이 올라오면 추가합니다.
  받치는 SQL 은 카탈로그 `chaeyeon089/missing_products` (base 갈래 포함,
  recsys_sql pytest 로 검증).
- `PROD-03` 응답은 명세 22장과 다릅니다. 원천(FoodKeeper)에 `temperature_min/max`,
  `instruction_text` 가 없고 `tips` 는 단일 텍스트입니다. 같은 상품에 (보관 장소,
  상황)별 지침이 여러 개라 `items[]` 목록으로 냅니다. 명세 수정 협의가 필요합니다.
- `RECO-01` 의 `recommendation.score` 는 현재 "그 버블의 후보 레시피 중 이 재료를
  쓰는 수" 입니다. 주문 로그가 쌓이면 인기도 점수로 교체합니다 (필드 계약 동일).
  존재하지 않는 `bubble_id` 는 404 입니다.
- `HOME-01` 버블은 후보 레시피 수가 하한 미달이면 `enabled=false` 로 내려갑니다.
  `type` 필드는 DB 내부 분류라 응답에서 제거했습니다 (FE 미사용 확인).
- `RECIPE-01` 의 `nutrition` 은 원본(jsonb) 키를 그대로 냅니다 (`protein_g`,
  `sodium_mg` 등, 원본마다 채워진 항목이 다름). 명세 17장의 calories/protein/
  carbs/fat 로 펴면 대부분 null 이 되어 키 통일은 협의 대상입니다. `steps[]` 는
  명세에 없지만 상세 화면이 항상 함께 쓰는 값이라 냅니다.
- 재구매 추천(`reorder-candidates`)은 보류 결정으로 서빙에서 내렸습니다. SQL 은
  `recsys_sql` 카탈로그(`_template/reorder_candidates.sql`)에 남아 있습니다.
- My냉장고 CRUD 는 AI 파트가 구현합니다. DELETE 는 HTTP 200 + envelope
  (`data: null`) 입니다.
- **품목 키는 `fridgeItemId` 가 아니라 `productId` 입니다** (명세 조정 필요).
  `user_fridge` 의 PK 가 (ingredient_id, user_id, product_id) 복합키라 단일
  품목 id 가 없습니다. 같은 이유로 목록의 `ingredient` 는 단수 객체가 아니라
  `ingredients[]` 배열입니다 (밀키트처럼 PRIMARY 재료가 여럿인 상품 대응).
- POST 는 세 경우를 409 `CONFLICT` 로 거절하고, `message` 로 구분합니다. 여러 경우에 걸치면
  **아래 순서의 첫 번째 하나만** 냅니다.
  1. `이미 냉장고에 담긴 상품입니다.` - 버튼을 두 번 눌러 동시에 들어온 경우도 이 메시지입니다.
     이미 담긴 상품이 나중에 판매 중지되어도 이 메시지입니다(품목은 냉장고에 그대로 보임).
  2. `판매가 중지된 상품이라 담을 수 없습니다.` - 판매 중지 상품(`is_active=false`).
     추천·구매 경로에서 빠지는 상품이라 새로 담는 것도 막습니다. 상세 조회는 그대로 됩니다.
  3. `재료 정보가 연결되지 않은 상품이라 담을 수 없습니다.` - 재료 미연결 상품(현재 295건).
- 기한 지난 품목도 목록에 나오며 `is_expired` 로 구분합니다.
- 목록의 `product.image_url` 은 상품 대표 이미지 URL 입니다(`product.image_url`, 0015).
  원천에 이미지가 없는 상품은 `null` 이라 FE 는 placeholder 를 둡니다.

## RECO-02 상세 (구현됨)

`GET /api/v1/recommendations/my-recipes`

- Header: `Authorization: Bearer <JWT>` (필수)
- Query: `limit` (1~50, 기본 10). 매칭률 하한은 서버 고정 0.5 (FE 합의로 파라미터 제거)
- SQL: 카탈로그 `my_recipe_candidates` (보유 판정: 냉장고 상품의 PRIMARY 재료 +
  상비재료, 만료 재료 제외)
- `recommendation_reason` 은 **앞 3장만 LLM 생성**이고 나머지는 규칙 기반 문장입니다
  (`rag_lab.reason_service`, #38).
  - LLM 문구: 존댓말 두 문장, 40~120자. 첫 문장은 가진 재료, 둘째 문장은 더 담을 재료입니다.
    다른 카드 재료 혼입, 지어낸 재료·조리시간, 보유/부족 뒤집힘, 영양·건강 주장, 개수 숫자는 자동 검사로 걸러집니다.
  - 규칙 문구: 한 문장, 60자 이내. 4장째부터, 그리고 LLM 이 시간 초과(카드당 2초)·오류·검사
    실패일 때 그 카드만 이 문구로 대체됩니다. 필드는 항상 채워져 있습니다.
  - 응답 시간: 규칙 문구만이면 약 0.3초, LLM 을 쓰면 약 1.7초입니다(2026-09-28 dev 측정).
- rate limit 은 사용자당 10/분입니다(다른 API 는 60/분).

```json
{
  "status": "SUCCESS",
  "message": "요청에 성공하였습니다.",
  "data": {
    "items": [
      {
        "recipe_id": 1001,
        "name": "돼지고기 김치찌개",
        "difficulty": "EASY",
        "cook_time_min": 15,
        "servings": 2,
        "recommendation_reason": "두부와 돼지고기가 넉넉해 칼칼한 찌개를 끓이기 좋은 조합입니다. 김치만 더 담으면 바로 완성돼요.",
        "match": {
          "required_ingredients": 5,
          "available_ingredients": 4,
          "missing_ingredients": 1,
          "match_rate": 0.8
        },
        "missing_ingredients": [
          { "ingredient_id": 2, "name": "김치" }
        ]
      }
    ]
  },
  "error": null,
  "timestamp": "2026-09-16T09:00:00Z"
}
```

## FAV / RECENT 상세 (구현됨)

My 레시피 화면 하단 "최근 본 레시피" / "찜한 레시피" 두 줄입니다. 모두 인증 필수.
표는 migration `0017_user_recipe_activity` 의 `user_recipe_favorite`, `user_recipe_view`
이고, 읽기 SQL 은 카탈로그 `chaeyeon089/user_favorite_recipes`, `user_recent_recipes`,
쓰기는 `serving/user_recipe_sql.py` 입니다.

- `GET /users/me/favorite-recipes?limit=` (1~100, 기본 50): 최근에 찜한 순.
- `POST /users/me/favorite-recipes/{recipe_id}`: 없는 레시피 404, 이미 찜한 레시피 409.
  응답 `data` 는 `{recipe_id, favorited_at}`.
- `DELETE /users/me/favorite-recipes/{recipe_id}`: 찜하지 않은 레시피 404.
  HTTP 200 + envelope(`data: null`).
- `GET /users/me/recent-recipes?limit=` (1~50, 기본 10): 마지막으로 본 순. 같은 레시피는
  한 번만 나옵니다.
- `POST /users/me/recent-recipes/{recipe_id}`: FE 가 상세 화면 진입 시 호출합니다. 다시 보면
  행이 늘지 않고 `viewed_at` 만 갱신되며, 사용자당 최근 100건만 보관합니다. 없는 레시피 404.
  응답 `data` 는 `{recipe_id, viewed_at}`.
- `DELETE /users/me/recent-recipes` body `{"recipe_ids": [...]}` (1~100건, 각 1 이상): 화면의
  "전체선택 -> 선택삭제" 가 체크된 id 를 한 번에 보냅니다. 기록에 없는 id 는 404 가 아니라
  건너뛰고, 응답 `data` 는 `{deleted_count}` (실제로 지운 건수, 중복 id 는 한 번만) 입니다.
  다른 사용자의 기록은 건드리지 않습니다.
- 상세 GET(`RECIPE-01`)에 조회 기록을 숨기지 않았습니다. 비로그인·프리페치 요청이 기록을
  오염시키지 않도록 조회와 기록을 분리합니다.

목록 항목은 두 API 가 같은 카드 모양(`recipe_id, name, image_url, difficulty, cook_time_min,
servings`)에 각각 `favorited_at` / `viewed_at` 을 더한 것입니다.

```json
{
  "status": "SUCCESS",
  "message": "요청에 성공하였습니다.",
  "data": {
    "items": [
      {
        "recipe_id": 1001,
        "name": "닭가슴살 샐러드",
        "image_url": "https://example.com/r.jpg",
        "difficulty": "EASY",
        "cook_time_min": 10,
        "servings": 1,
        "favorited_at": "2026-09-29T09:00:00Z"
      }
    ]
  },
  "error": null,
  "timestamp": "2026-09-29T09:00:00Z"
}
```
