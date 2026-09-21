# Product 이미지 백필

## 왜 다시 받아야 하나

`product`에는 이미지 원천이 없다. `metadata`에 남은 것은 `brand`, `source_url`,
`crawled_at`, `stock_source`뿐이고 이미지 키는 하나도 없다. 로컬 크롤 산출물
(`data/raw/kurly/**/*.jsonl`, product 레코드 1,942건)도 `image_urls`가 전부 빈 배열이다.
`demo_product_search.jsonl`에 `listImageUrl`이 있지만 89개 검색 결과라 적재된 카탈로그와는
1건만 겹친다.

그래서 `metadata.source_url`을 열쇠로 상품 API에서 다시 받는다.

## 상품 수가 아니라 페이지 수만큼 받는다

| | 수 |
| --- | ---: |
| `product` 행 | 2,554 |
| `source_url`에서 페이지 번호를 꺼낼 수 있는 행 | 2,553 |
| **고유 페이지** | **1,002** |

한 상품 페이지의 variant(용량·구성 선택)가 각각 별도 `product` 행이라 2.5행이 한 페이지를
공유한다. 페이지 단위로 한 번만 받고 같은 페이지를 쓰는 행에 같은 대표 이미지를 넣는다.
상품 수만큼 요청하면 1,551건을 헛으로 보낸다.

`source_product_id`를 먼저 보면 안 된다. variant 행에는 페이지 번호가 아니라 deal 번호가
들어 있다(2,553행 중 1,551행). 페이지 번호는 `source_url`의 `/goods/{번호}`에서 꺼내고,
주소가 아예 없는 행에서만 `source_product_id`로 물러선다. 맞으면 받아지고 아니면 404 로
떨어져 그 행은 비워 둔다. 틀린 이미지가 붙을 길은 없다.

```text
product 1164  source_product_id=10153238  source_url=.../goods/5153238   <- 다름
product 1547  source_product_id=5063827   source_url=.../goods/5063827   <- 같음
```

## 받는 값

`GET https://api.kurly.com/showroom/v2/products/{goods_no}`의 `data`에서 한 장을 고른다.
`main_image_url` → `original_image_url` → `share_image_url` 순이고, `http`로 시작하지
않으면 쓰지 않는다. 상세·브랜드·후기 이미지는 가져오지 않는다.

## 실행

기본은 DB를 바꾸지 않는 dry-run이다.

```bash
uv run python scripts/db/backfill_product_images.py \
  --target production --target-url-env DATABASE_URL
```

적용은 확인 문자열을 요구한다.

```bash
uv run python scripts/db/backfill_product_images.py \
  --target production --target-url-env DATABASE_URL \
  --apply --confirm-production BACKFILL_PRODUCT_IMAGES_V1
```

| 옵션 | 뜻 |
| --- | --- |
| `--cache` | 받은 이미지를 남길 JSONL. 기본 `data/raw/kurly/product_images.jsonl` |
| `--sleep-seconds` | 요청 간격. 기본 0.25초 |
| `--limit` | 받을 페이지 수 상한. 프로브용 |
| `--retries` | 재시도 횟수. 간격을 2배씩 늘린다. 없는 상품(404/410)은 재시도하지 않는다 |

## 다시 돌려도 되는 이유

받은 페이지는 캐시에 쌓이고 다음 실행은 건너뛴다. 중간에 끊겨도 이어서 받고, DB만 다시
맞출 때는 요청을 하나도 보내지 않는다. 쓰기는 값이 실제로 달라지는 행만 고르므로 두 번째
실행은 `pending_updates=0`이다.

## 한계

- 한 페이지의 variant는 같은 대표 이미지를 갖는다. variant별 사진이 다른 상품은 구분하지 않는다.
- `source_url`이 없는 행은 `source_product_id`로 한 번 시도하고, 그것도 아니면 비워 둔다.
- 받지 못한 페이지의 행은 건드리지 않는다. 실패 목록은 실행 결과에 남는다.

## 2026-09-21 Production 적용 결과

```
products=2554 pages=1003 images=980 failed_pages=23
products_without_page_key=0
updated=2500
```

| 확인 | 값 |
| --- | ---: |
| `image_url` 채워짐 | 2,500 / 2,554 (97%) |
| 미채움 | 54 — 전부 Kurly 404 (내려간 상품 23페이지) |
| `http`로 시작하지 않는 값 / 빈 문자열 | 0 / 0 |
| 데모 상품 6건 | 전부 채워짐 |
| 재실행 | `pending_updates=0 updated=0` |

실패 23건은 API가 404를 주는 페이지다. 버그가 아니라 원천이 사라진 것이므로 비워 둔다.

## 서빙 노출

이 백필은 컬럼만 채운다. `product_detail.sql`과 Serving 응답 모델은 아직 `image_url`을
읽지 않는다(`product_detail.sql` 주석 참고). 화면에 내보내려면 그 두 곳을 함께 고쳐야 하며
별도 작업이다.
