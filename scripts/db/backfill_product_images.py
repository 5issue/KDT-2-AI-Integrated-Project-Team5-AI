"""Kurly 상품 API에서 대표 이미지 URL을 받아 `product.image_url`을 채웁니다.

`product`에는 이미지 원천이 없습니다. `metadata.source_url`에 상품 페이지 주소만 있어서
이미지를 다시 받아와야 합니다. 로컬 크롤 산출물에는 `image_urls`가 전부 빈 배열입니다.

상품 2,554행이 고유 페이지 1,002개를 공유합니다. 한 페이지의 variant(용량·구성 선택)가
각각 별도 `product` 행이기 때문입니다. 그래서 페이지 단위로 한 번만 받고, 같은 페이지를
쓰는 행에 같은 대표 이미지를 넣습니다. 상품 수만큼 요청하면 2.5배를 헛으로 보냅니다.

받은 응답은 `--cache`에 남깁니다. 다시 돌릴 때 이미 받은 페이지는 건너뛰므로, 중간에
끊겨도 이어서 받고 DB만 다시 맞출 때는 요청을 하나도 보내지 않습니다.

기본 동작은 DB를 바꾸지 않는 dry-run이며 Production 적용에는 확인 문자열이 필요합니다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import psycopg

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_CONFIRMATION = "BACKFILL_PRODUCT_IMAGES_V1"
Target = Literal["local", "production"]

DETAIL_URL = "https://api.kurly.com/showroom/v2/products/{goods_no}"
GOODS_PATTERN = re.compile(r"/goods/(\d+)")
# 대표 -> 원본 -> 공유 순으로 봅니다. 상품 카드가 쓰는 것은 대표 이미지 한 장입니다.
IMAGE_FIELDS = ("main_image_url", "original_image_url", "share_image_url")


@dataclass(frozen=True, slots=True)
class ProductRow:
    """대상 상품 한 행입니다."""

    product_id: int
    source_url: str | None
    source_product_id: str | None
    image_url: str | None

    @property
    def goods_no(self) -> str | None:
        """Kurly 상품 페이지 번호를 고릅니다.

        주소가 있으면 거기서 꺼냅니다. `source_product_id`를 먼저 보면 안 됩니다. variant
        행에는 페이지 번호가 아니라 deal 번호가 들어 있어(2,553행 중 1,551행) 찾지 못합니다.

        주소가 아예 없는 행에서만 `source_product_id`로 물러섭니다. 맞으면 받아지고 아니면
        404 로 떨어져 그 행은 비워 둡니다. 틀린 이미지가 붙을 길은 없습니다.
        """
        match = GOODS_PATTERN.search(self.source_url or "")
        if match:
            return match.group(1)
        if self.source_product_id and self.source_product_id.isdigit():
            return self.source_product_id
        return None


def load_env(path: Path) -> dict[str, str]:
    """비밀 값을 출력하지 않고 단순 env 파일을 읽습니다."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_url(values: dict[str, str], key: str) -> str:
    """환경 변수 또는 env 파일에서 접속 문자열을 읽습니다. 값 자체는 출력하지 않습니다."""
    value = os.environ.get(key) or values.get(key)
    if not value:
        raise ValueError(f"{key}가 설정되지 않았습니다. 접속 문자열 값은 출력하지 않습니다.")
    return value


def validate_confirmation(target: Target, apply: bool, confirmation: str | None) -> None:
    """Production 에 실제로 쓰기 전 확인 문자열을 요구합니다."""
    if target == "production" and apply and confirmation != PRODUCTION_CONFIRMATION:
        raise ValueError(f"Production 적용에는 --confirm-production {PRODUCTION_CONFIRMATION} 이 필요합니다.")


def fetch_products(url: str) -> list[ProductRow]:
    """대상 DB에서 상품과 현재 이미지 값을 읽습니다."""
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'product' AND column_name = 'image_url'"
        )
        if cursor.fetchone() is None:
            raise ValueError("product.image_url 이 없습니다. migration 0015 를 먼저 적용하세요.")
        cursor.execute(
            "SELECT product_id, metadata->>'source_url', source_product_id, image_url FROM product ORDER BY product_id"
        )
        return [ProductRow(int(row[0]), row[1], row[2], row[3]) for row in cursor.fetchall()]


def pick_image(payload: dict[str, Any]) -> str | None:
    """응답에서 대표 이미지 한 장을 고릅니다."""
    data = payload.get("data") or payload
    if not isinstance(data, dict):
        return None
    for field in IMAGE_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


def load_cache(path: Path) -> dict[str, str]:
    """이미 받아 둔 페이지의 이미지를 읽습니다."""
    cached: dict[str, str] = {}
    if not path.is_file():
        return cached
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        goods_no, image_url = record.get("goods_no"), record.get("image_url")
        if goods_no and image_url:
            cached[str(goods_no)] = image_url
    return cached


def fetch_image(goods_no: str, *, timeout: float, retries: int) -> str | None:
    """상품 페이지 하나의 대표 이미지를 받아옵니다. 없는 상품은 재시도하지 않습니다."""
    request = urllib.request.Request(
        DETAIL_URL.format(goods_no=goods_no),
        headers={"User-Agent": "Mozilla/5.0", "X-Kurly-Session-Id": "1", "Accept": "application/json"},
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return pick_image(json.load(response))
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                return None
            if attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == retries:
                raise
        # 재시도는 점점 뜸하게 합니다. 상대 서버를 몰아붙이지 않습니다.
        time.sleep(2**attempt)
    return None


def collect_images(
    goods_numbers: list[str], cache_path: Path, *, sleep_seconds: float, timeout: float, retries: int
) -> tuple[dict[str, str], list[str]]:
    """받지 않은 페이지만 차례로 받아 캐시에 덧붙입니다."""
    images = load_cache(cache_path)
    pending = [goods_no for goods_no in goods_numbers if goods_no not in images]
    failed: list[str] = []
    if not pending:
        return images, failed

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as cache_file:
        for index, goods_no in enumerate(pending):
            try:
                image_url = fetch_image(goods_no, timeout=timeout, retries=retries)
            except Exception:  # noqa: BLE001 - 한 페이지 실패로 전체를 멈추지 않습니다.
                failed.append(goods_no)
                continue
            if image_url is None:
                failed.append(goods_no)
                continue
            images[goods_no] = image_url
            cache_file.write(json.dumps({"goods_no": goods_no, "image_url": image_url}, ensure_ascii=False) + "\n")
            cache_file.flush()
            if index % 100 == 99:
                print(f"  진행 {index + 1}/{len(pending)}")
            time.sleep(sleep_seconds)
    return images, failed


def plan_updates(products: list[ProductRow], images: dict[str, str]) -> list[tuple[int, str]]:
    """값이 실제로 달라지는 행만 고릅니다. 같은 값을 다시 쓰지 않습니다."""
    updates: list[tuple[int, str]] = []
    for product in products:
        goods_no = product.goods_no
        if goods_no is None:
            continue
        image_url = images.get(goods_no)
        if image_url and image_url != product.image_url:
            updates.append((product.product_id, image_url))
    return updates


def write_updates(url: str, updates: list[tuple[int, str]]) -> int:
    """한 트랜잭션에서 이미지 URL을 씁니다."""
    if not updates:
        return 0
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "UPDATE product SET image_url = %s, updated_at = now() WHERE product_id = %s",
                [(image_url, product_id) for product_id, image_url in updates],
            )
        connection.commit()
    return len(updates)


def parse_args() -> argparse.Namespace:
    """명령행 인자를 읽습니다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target", choices=("local", "production"), required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "data/raw/kurly/product_images.jsonl")
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="받을 페이지 수 상한. 0 이면 전부")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production")
    return parser.parse_args()


def main() -> None:
    """이미지를 모아 보고하고, --apply 일 때만 대상 DB에 씁니다."""
    args = parse_args()
    target = cast(Target, args.target)
    validate_confirmation(target, args.apply, args.confirm_production)
    values = load_env(args.env_file)
    target_url = env_url(values, args.target_url_env)

    products = fetch_products(target_url)
    goods_numbers = sorted({goods_no for product in products if (goods_no := product.goods_no)})
    if args.limit:
        goods_numbers = goods_numbers[: args.limit]

    images, failed = collect_images(
        goods_numbers, args.cache, sleep_seconds=args.sleep_seconds, timeout=args.timeout, retries=args.retries
    )
    updates = plan_updates(products, images)
    without_key = [product.product_id for product in products if product.goods_no is None]

    print(f"mode={'APPLIED' if args.apply else 'DRY_RUN'} target={target}")
    print(f"products={len(products)} pages={len(goods_numbers)} images={len(images)} failed_pages={len(failed)}")
    print(f"products_without_page_key={len(without_key)}")
    print(f"pending_updates={len(updates)}")
    print(f"cache={args.cache}")
    if failed:
        print(f"failed_sample={failed[:10]}")
    if not args.apply:
        return
    print(f"updated={write_updates(target_url, updates)}")


if __name__ == "__main__":
    main()
