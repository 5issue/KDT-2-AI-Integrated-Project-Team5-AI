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
| 429 | `TOO_MANY_REQUESTS` | rate limit 초과 (AI 파트 확장) |
| 500 | `INTERNAL_SERVER_ERROR` | 처리되지 않은 예외. 상세는 응답에 싣지 않음 |
| 503 | `SERVICE_UNAVAILABLE` | DB 미연결 등 (AI 파트 확장) |

## 인증

Next.js BFF(서버)가 FastAPI 를 호출하는 구조입니다. 사용자 식별은 `X-User-Id`
헤더로 하며(`serving/src/serving/auth.py`), 공유 시크릿 검증 방식과 최종 헤더
이름은 FE 와 협의 중입니다. 확정되면 `auth.py` 만 교체합니다.
경로에 user id 를 받는 방식은 IDOR 위험이 있어 쓰지 않습니다.

## 엔드포인트

Base URL: `/api/v1`

| Index | 기능 | 메서드 | 경로 | 상태 |
| --- | --- | --- | --- | --- |
| HOME-01 | 홈 버블 목록 | GET | `/home/bubbles` | 구현됨 |
| RECO-01 | 버블 기반 상품 추천 | GET | `/recommendations/products` | 기획 |
| RECO-02 | My냉장고 기반 레시피 추천 | GET | `/recommendations/my-recipes` | 구현됨 |
| PROD-01 | 상품 상세 | GET | `/products/{productId}` | 구현됨 |
| PROD-02 | 상품으로 만들 수 있는 레시피 | GET | `/products/{productId}/recipes` | 구현됨 |
| PROD-03 | 상품 보관 가이드 | GET | `/products/{productId}/storage-guide` | 구현됨 (명세 조정 필요) |
| RECIPE-01 | 레시피 상세 | GET | `/recipes/{recipeId}` | 기획 |
| RECIPE-02 | 부족 재료 계산 | GET | `/recipes/{recipeId}/missing-ingredients` | 기획 |
| RECIPE-03 | 부족 재료 상품 추천 | GET | `/recipes/{recipeId}/missing-products` | 명세 통일 대기 |
| FRIDGE-01 | My냉장고 품목 목록 | GET | `/users/me/fridge` | 기획 |
| FRIDGE-02 | My냉장고 품목 추가 | POST | `/users/me/fridge` | 기획 |
| FRIDGE-03 | My냉장고 품목 수정 | PATCH | `/users/me/fridge/{fridgeItemId}` | 기획 |
| FRIDGE-04 | My냉장고 품목 삭제 | DELETE | `/users/me/fridge/{fridgeItemId}` | 기획 |

- `RECIPE-03` 은 명세 30장 목록에는 있으나 본문 정의가 없어, `missing-ingredients`
  와의 통일 결정을 기다립니다.
- `PROD-03` 응답은 명세 22장과 다릅니다. 원천(FoodKeeper)에 `temperature_min/max`,
  `instruction_text` 가 없고 `tips` 는 단일 텍스트입니다. 같은 상품에 (보관 장소,
  상황)별 지침이 여러 개라 `items[]` 목록으로 냅니다. 명세 수정 협의가 필요합니다.
- `HOME-01` 버블은 후보 레시피 수가 하한 미달이면 `enabled=false` 로 내려갑니다.
  `type` 은 현재 `RECIPE` 고정입니다.
- 재구매 추천(`reorder-candidates`)은 보류 결정으로 서빙에서 내렸습니다. SQL 은
  `recsys_sql` 카탈로그(`_template/reorder_candidates.sql`)에 남아 있습니다.
- My냉장고 CRUD 는 AI 파트가 구현합니다. DELETE 는 HTTP 200 + envelope
  (`data: null`) 입니다.

## RECO-02 상세 (구현됨)

`GET /api/v1/recommendations/my-recipes`

- Header: `X-User-Id` (필수)
- Query: `min_match_rate` (0~1, 기본 0.5), `limit` (1~50, 기본 10)
- SQL: 카탈로그 `my_recipe_candidates` (보유 판정: 냉장고 상품의 PRIMARY 재료 +
  상비재료, 만료 재료 제외)
- `recommendation_reason` 은 현재 규칙 기반 문장입니다
  (`schemas._build_reason`). LLM 생성으로 바꿀지는 협의 대상입니다.

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
        "recommendation_reason": "김치만 있으면 만들 수 있어요",
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
