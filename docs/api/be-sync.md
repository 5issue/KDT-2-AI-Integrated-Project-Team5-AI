# BE 연동 정리 (상품 id·유저 id·냉장고·가격 재고)

2026-09-29 BE 답변(product-service, order-service, auth)에 맞춰 AI 서버 쪽에서 정한 것과
BE 에 넘기는 것을 한 곳에 둡니다. 코드가 바뀌면 이 문서를 같이 고칩니다.

| 논점             | 결정                                                                                     | AI 쪽 산출물                               |
| ---------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------ |
| 1. 상품 id 매핑  | BE 가 AI 상품 DML 을 그대로 적재해 **product_id 를 같은 값**으로 맞춘다. 매핑 컬럼 없음  | `data_pipeline/sql/export/product_dml.sql` |
| 2. 유저 id       | JWT `sub`(user_db `users.id`)를 그대로 사용자 id 로 쓴다. 서명은 auth 서버 JWKS 로 검증  | `serving/src/serving/auth.py`              |
| 3. My냉장고 원천 | 배송완료 Admin API 가 `POST /internal/fridge/items` 를 관리자 JWT 로 호출한다 (RabbitMQ 소비는 안 함) | `serving/src/serving/routers/internal.py`  |
| 5. 상품 조회     | AI 응답의 `product_id` 로 BE `GET /products/by-ai` 를 불러 장바구니에 담는다 (FE 경로)    | 없음 (BE API, 5절에 기록)                  |
| 4. 가격·재고     | price 는 DML 로 같아진다. stock_quantity 는 초기값만 같고 이후 갈라짐. 화면 표시는 BE 값 | 없음 (포기)                                |

## 1. 상품 DML

`uv run data-pipeline export-products` 가 `category`·`product` 를 읽어
`data_pipeline/sql/export/product_dml.sql` 로 씁니다 (읽기만 하고 DB 는 바꾸지 않습니다).

**정본은 production 백업에서 만듭니다.** 팀장이 BE 에 넘긴 `production_backup.sql`(plain pg_dump)
을 `--from-dump <경로>` 로 넘기면 그 파일의 COPY 블록에서 읽습니다. 2026-09-30 대조 결과 dev 브랜치와
백업은 상품 2,554행의 id·가격·재고·이름·카테고리·보관·브랜드가 전부 같고, `image_url` 만 dev 는 0건,
백업은 2,500건이라 백업으로 만든 파일을 커밋했습니다. 옵션 없이 돌리면 `DATABASE_URL` 의 DB 를 읽습니다.

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
- **처음 쓰는 사용자는 AI DB 에 자동 등록합니다.** AI DB 의 `app_user` 는 id 와 등록 시각만 가진 표이고,
  냉장고(`user_fridge`)·찜·최근 본 기록이 FK 로 가리킵니다. BE 사용자를 미리 옮겨 두지 않고, 쓰기 요청
  (냉장고 담기, 찜, 최근 본 기록)이 오면 그 직전에 `sub` 를 등록합니다(`serving/src/serving/app_user_sql.py`).
  등록하지 않으면 BE 사용자의 첫 냉장고 담기가 FK 위반(500)이 됩니다. 사용자 정보의 정본은
  BE user DB 이며 AI DB 는 id 외에 아무것도 저장하지 않습니다.

서빙 환경변수 (`serving/.env.example`):

| 키                       | 값                                            | 비고                                                       |
| ------------------------ | --------------------------------------------- | ---------------------------------------------------------- |
| `JWT_JWKS_URL`           | `http://<auth-service>/.well-known/jwks.json` | 비우면 X-User-Id 방식(로컬 전용)                           |
| `JWT_ISSUER`             | auth 서버의 `iss` 값                          | 비우면 검사하지 않음. **BE 값 확인 필요**                  |
| `JWT_AUDIENCE`           | AI 서버용 `aud` 값                            | 비우면 검사하지 않음. BE 가 aud 를 넣지 않으면 비워 둠     |
| `JWT_ALGORITHMS`         | `RS256` (기본)                                | 쉼표 구분. BE 가 ES256 등을 쓰면 바꿈. **BE 값 확인 필요** |
| `JWT_LEEWAY_SECONDS`     | `30`                                          | 시계 오차 허용                                             |
| `JWT_JWKS_CACHE_SECONDS` | `300`                                         | JWKS 캐시 수명                                             |

BE 에 받아야 하는 값 세 가지: **JWKS URL(클러스터 내부 주소), `iss`, 서명 알고리즘**. `aud` 는
쓰고 있다면 함께. -> 디스코드에 토론게시판에 클라우드팀이 공유하였으므로 참고합니다.

## 3. My냉장고 채우기 (BE -> AI)

배송완료 Admin API(BE)가 주문 품목을 모아 아래 내부 엔드포인트를 한 번 부릅니다 (2026-09-30 BE 협의).
사용자 JWT 를 전파하는 방식(`POST /users/me/fridge` 건당 호출)은 배송완료 시점에 사용자 토큰이
없어 쓰지 않고, **관리자 role 의 JWT** 로 부릅니다. 공유 시크릿 헤더는 만들지 않았습니다.

```
POST /api/v1/internal/fridge/items
Authorization: Bearer <관리자 JWT (role == "ADMIN")>
Content-Type: application/json

{
  "user_id": 42,
  "items": [
    {"product_id": 749, "quantity": 1, "unit": "개", "expires_at": null},
    {"product_id": 810, "quantity": 2, "unit": "팩"}
  ]
}
```

- 인증: 2절과 같은 JWKS 서명 검증을 거친 뒤 `role` 클레임이 `ADMIN` 인지 봅니다(클레임이 문자열이면
  같은 값, 배열이면 포함). 토큰이 없거나 틀리면 401, 관리자가 아니면 403 `FORBIDDEN`. 클레임 이름과
  값은 `JWT_ADMIN_ROLE_CLAIM`(기본 `role`)·`JWT_ADMIN_ROLE`(기본 `ADMIN`)로 바꿀 수 있습니다.
  **BE 확인 필요**: 관리자 토큰의 role 클레임 이름과 값이 정확히 `role` / `"ADMIN"` 인지.
- `user_id` 는 대상 사용자(users.id). 토큰의 `sub` 는 관리자 자신이라 쓰지 않습니다.
  처음 보는 사용자는 `app_user` 에 자동 등록합니다(2절).
- `items` 는 1~100 개, `product_id` 는 1절의 공통 id 이고 **한 요청 안에서 중복 불가**(422). BE 는
  같은 상품의 주문 라인을 합쳐 보냅니다. `quantity` 는 0 보다 큰 수, `unit` 은 1~20자,
  `expires_at` 은 ISO-8601 또는 `null`(기한 없음). 키를 생략하면 기존 기한을 유지합니다.
- upsert 규칙: 냉장고에 없는 상품은 새로 담고(`inserted`), 이미 있는 상품은 **수량을 더합니다**
  (`updated`). `unit` 은 새 값으로 바꾸고 `expires_at` 은 보낸 경우만 바꿉니다. 사용자가 실제로
  산 상품이라 판매 중지(`is_active=false`)는 보지 않습니다.
- 응답 200: 성공 품목은 `items[]`, 담지 못한 품목은 `skipped[]` 에 사유를 적어 냅니다. 둘을 합치면
  요청 품목 전체이고 응답은 `product_id` 오름차순입니다. 없는 상품("상품을 찾을 수 없습니다.")과
  재료 미연결 상품("재료 정보가 연결되지 않은 상품이라 담을 수 없습니다.")은 거절(4xx)하지 않고
  `skipped` 로 갑니다. BE 는 기록만 하면 됩니다.

```json
{
  "status": "SUCCESS",
  "message": "요청에 성공하였습니다.",
  "data": {
    "user_id": 42,
    "items": [
      {"product_id": 749, "action": "inserted", "quantity": 1.0, "unit": "개", "expires_at": null},
      {"product_id": 810, "action": "updated", "quantity": 3.0, "unit": "팩", "expires_at": "2026-10-10T00:00:00Z"}
    ],
    "skipped": [{"product_id": 999, "reason": "상품을 찾을 수 없습니다."}]
  },
  "error": null,
  "timestamp": "2026-10-01T03:00:00Z"
}
```

- 품목 전체가 한 트랜잭션입니다. 중간에 DB 오류가 나면 전부 되돌리고 500 이라 통째로 재시도하면 됩니다.
  **200 을 받은 뒤 재시도하면 수량이 두 번 더해지므로** 5xx·네트워크 오류에만 재시도합니다.
  주문 단위 멱등 키가 필요해지면 그때 `Idempotency-Key` 헤더를 더합니다.
- rate limit 은 관리자 `sub` 기준 분당 60 회(기본값)입니다. 주문당 1회 호출이면 충분하고, 모자라면
  `RATE_LIMIT_PER_MINUTE` 을 올립니다.
- 사용자 본인용 CRUD(`/users/me/fridge`, FRIDGE-01~04)는 그대로 FE 가 씁니다. 시연에서는 BE 호출 없이
  `seed-demo` 로 직접 채워도 됩니다.
- 로컬(`JWT_JWKS_URL` 비움)에서는 role 검사 없이 `X-User-Id`(호출자 id)만 봅니다.

RabbitMQ 소비 방식은 AI 서버에 컨슈머 프로세스를 하나 더 두는 일이라 이번 범위에서 뺐습니다.

## 4. 가격·재고

- `price`: DML 로 같아지고 이후 바뀔 일이 거의 없습니다. 바뀌면 1절 명령으로 다시 넘깁니다.
- `stock_quantity`: 초기값만 같습니다. AI 서버는 이 값을 "살 수 있는 상품만 추천" 필터로만 쓰고,
  화면의 재고 표시·품절 처리는 BE 값을 씁니다. 정합성 맞추기는 프로젝트 기간 안에 하지 않습니다.

## 5. AI 추천 상품 -> 장바구니 (BE 상품 조회 API, 참고)

AI 응답(`RECO-01`, `RECIPE-03`, `PROD-*`)의 `product_id` 는 1절 DML 로 BE 에 적재된 값이라,
FE 가 그 id 로 BE 상품을 조회해 장바구니에 담습니다. BE(product-service)가 2026-09-30 공유한 명세를
그대로 옮겨 둡니다. AI 서버 코드 변경은 없습니다.

```
GET /api/v1/products/by-ai?ai_product_ids=755,810
GET /api/v1/products/by-ai?ai_product_ids=755&ai_product_ids=810
Auth: 없음
```

- `ai_product_ids` 는 AI 응답의 `product_id` 입니다. 쉼표 구분과 반복 파라미터 둘 다 받습니다.
- 응답 `data.items[]` 는 `aiProductId`(= AI `product_id`), BE 쪽 `id`·`skuCode`·`type`·`parentId`·
  `name`·`brand`·`price`·`salePrice`·`discountRate`·`status`·`thumbnailUrl` 입니다. 장바구니 담기는
  BE `id` 로 합니다. BE 에 없는 id 는 `data.notFoundAiProductIds[]` 로 옵니다.
- 화면의 가격·할인·재고 상태·썸네일은 이 응답(BE 값)을 쓰고, AI 응답의 `price`·`stock_quantity`·
  `image_url` 은 참고값입니다(4절).

```json
{
  "status": "SUCCESS",
  "message": "요청에 성공하였습니다.",
  "data": {
    "items": [
      {
        "aiProductId": 755,
        "id": 156062,
        "skuCode": "M00000760247",
        "type": "UNIT",
        "parentId": 155058,
        "name": "프링글스 사워크림앤어니언 53g",
        "brand": "프링글스",
        "price": 2000,
        "salePrice": 1840,
        "discountRate": 8,
        "status": "SALE",
        "thumbnailUrl": "https://product-image.kurly.com/.../image.jpg"
      }
    ],
    "notFoundAiProductIds": [810]
  },
  "error": null,
  "timestamp": "2026-09-30T08:38:21Z"
}
```
