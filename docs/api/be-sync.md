# BE 연동 정리 (상품 id·유저 id·냉장고·가격 재고)

2026-09-29 BE 답변(product-service, order-service, auth)에 맞춰 AI 서버 쪽에서 정한 것과
BE 에 넘기는 것을 한 곳에 둡니다. 코드가 바뀌면 이 문서를 같이 고칩니다.

| 논점 | 결정 | AI 쪽 산출물 |
| --- | --- | --- |
| 1. 상품 id 매핑 | BE 가 AI 상품 DML 을 그대로 적재해 **product_id 를 같은 값**으로 맞춘다. 매핑 컬럼 없음 | `data_pipeline/sql/export/product_dml.sql` |
| 2. 유저 id | JWT `sub`(user_db `users.id`)를 그대로 사용자 id 로 쓴다. 서명은 auth 서버 JWKS 로 검증 | `serving/src/serving/auth.py` |
| 3. My냉장고 원천 | 결제 완료 후 BE 가 `POST /users/me/fridge` 를 건당 호출한다 (RabbitMQ 소비는 안 함) | 기존 API, 아래 계약 |
| 4. 가격·재고 | price 는 DML 로 같아진다. stock_quantity 는 초기값만 같고 이후 갈라짐. 화면 표시는 BE 값 | 없음 (포기) |

## 1. 상품 DML

`uv run data-pipeline export-products` 가 dev DB 의 `category`·`product` 를 읽어
`data_pipeline/sql/export/product_dml.sql` 로 씁니다 (읽기만 하고 DB 는 바꾸지 않습니다).

- 표준 SQL `INSERT ... VALUES (...), (...)` 이며 PostgreSQL 전용 문법이 없어 BE DB 종류와 무관합니다.
- 컬럼: `category(category_id, category_type, parent_id, name, depth)`,
  `product(product_id, sku, name, category_id, product_type, storage_type, origin_country, weight_g,
  unit_count, price, stock_quantity, is_active, brand_name, image_url, source_url)`.
  파일 머리 주석에 타입과 enum 값이 있습니다. `metadata`·`embedding`·시각 컬럼은 내지 않습니다.
- `product_id` 와 `price` 는 AI 응답(`RECO-01`, `RECIPE-03`, `PROD-*`)의 값과 같습니다.
  BE 가 이 id 를 PK 로 그대로 쓰면 "부족 재료 담기 -> 장바구니" 가 매핑 없이 이어집니다.
- `stock_quantity` 는 데모 시드가 채운 초기값입니다 (`NULL` 은 수량 미상, `0` 은 품절).
  주문이 나면 BE 와 갈라지며, AI 응답의 재고는 "판매 가능 여부 필터" 용도로만 씁니다.
- 상품 데이터가 바뀌면 같은 명령으로 다시 만들어 넘깁니다. 파일은 레포에 두고 커밋합니다.

BE 가 제안한 "AI 는 id 목록만 주고 FE 가 BE 에서 상품을 조회" 방식은 지금 응답 계약에도
그대로 적용됩니다. `RECIPE-03`·`RECO-01` 의 상품 카드에 `product_id` 가 있으니 FE 가 표시값
(가격·재고·이미지)을 BE 에서 다시 읽어도 됩니다. AI 응답의 가격·재고를 화면에 그대로 내지
않기로 하면 그 필드는 참고값으로 남습니다.

## 2. 유저 식별 (JWT)

BE 정책: 동기 호출은 JWT 원문을 전파하고 각 서비스가 auth 서버의
`GET /.well-known/jwks.json` 으로 서명을 검증합니다. AI 서버도 같은 방식으로 맞췄습니다.

- 요청: `Authorization: Bearer <JWT>`. 이전의 `X-User-Id` 헤더는 JWT 가 켜지면 **무시**합니다.
- 검증: 서명(JWKS, `kid` 로 키 선택, 키 교체 시 자동 재조회), `exp`, 설정하면 `iss`·`aud`.
  실패는 모두 401 `UNAUTHORIZED` 이고 사유는 서버 로그에만 남깁니다. JWKS 를 받지 못하면 503.
- 사용자 id: `sub` 를 양의 정수로 읽습니다. `sub` 가 정수 문자열이 아니면 401 입니다.
  **BE 확인 필요**: `sub` 가 `users.id` 숫자 그대로인지, 다른 형식(UUID 등)이면 어느 클레임에
  숫자 id 가 있는지.
- 비로그인 허용 경로(`RECIPE-03`)는 헤더가 없으면 익명, 있는데 틀리면 401 입니다.
- rate limit 과 액세스 로그의 사용자 키도 토큰의 `sub` 입니다.

서빙 환경변수 (`serving/.env.example`):

| 키 | 값 | 비고 |
| --- | --- | --- |
| `JWT_JWKS_URL` | `http://<auth-service>/.well-known/jwks.json` | 비우면 X-User-Id 방식(로컬 전용) |
| `JWT_ISSUER` | auth 서버의 `iss` 값 | 비우면 검사하지 않음. **BE 값 확인 필요** |
| `JWT_AUDIENCE` | AI 서버용 `aud` 값 | 비우면 검사하지 않음. BE 가 aud 를 넣지 않으면 비워 둠 |
| `JWT_ALGORITHMS` | `RS256` (기본) | 쉼표 구분. BE 가 ES256 등을 쓰면 바꿈. **BE 값 확인 필요** |
| `JWT_LEEWAY_SECONDS` | `30` | 시계 오차 허용 |
| `JWT_JWKS_CACHE_SECONDS` | `300` | JWKS 캐시 수명 |

BE 에 받아야 하는 값 세 가지: **JWKS URL(클러스터 내부 주소), `iss`, 서명 알고리즘**. `aud` 는
쓰고 있다면 함께.

## 3. My냉장고 채우기 (BE -> AI)

결제 완료 후 BE(order-service)가 주문 품목마다 아래를 부릅니다. 사용자의 JWT 를 그대로
전파하므로(동기 호출 정책) 서비스 계정 토큰이 따로 필요 없습니다.

```
POST /api/v1/users/me/fridge
Authorization: Bearer <사용자 JWT>
Content-Type: application/json

{"product_id": 749, "quantity": 1, "unit": "개", "expires_at": null}
```

- `product_id` 는 1절의 공통 id 입니다. `quantity` 는 0 보다 큰 수, `unit` 은 1~20자 문자열
  (`개`, `g`, `팩` 등 BE 가 가진 단위 그대로), `expires_at` 은 ISO-8601 또는 `null`.
- 응답 200 `{product_id, ...}`. 없는 상품은 404. 같은 상품이 이미 담겨 있으면 409 `CONFLICT`
  (`message: "이미 냉장고에 담긴 상품입니다."`) 이며 **재시도해도 안전**합니다. BE 는 409 를
  성공으로 간주하면 됩니다. 판매 중지 상품·재료 미연결 상품도 409 이고 message 로 구분합니다.
- 냉장고는 상품 단위 한 칸이라 같은 상품을 두 번 사도 한 칸입니다. 수량을 더하려면
  `PATCH /users/me/fridge/{product_id}` 로 `quantity` 를 올립니다 (BE 가 원하면 그렇게).
- 시연에서는 BE 호출 없이 `seed-demo` 로 직접 채워도 됩니다.

RabbitMQ 소비 방식은 AI 서버에 컨슈머 프로세스를 하나 더 두는 일이라 이번 범위에서 뺐습니다.
BE 가 건당 호출을 못 하게 되면 그때 다시 봅니다.

## 4. 가격·재고

- `price`: DML 로 같아지고 이후 바뀔 일이 거의 없습니다. 바뀌면 1절 명령으로 다시 넘깁니다.
- `stock_quantity`: 초기값만 같습니다. AI 서버는 이 값을 "살 수 있는 상품만 추천" 필터로만 쓰고,
  화면의 재고 표시·품절 처리는 BE 값을 씁니다. 정합성 맞추기는 프로젝트 기간 안에 하지 않습니다.
