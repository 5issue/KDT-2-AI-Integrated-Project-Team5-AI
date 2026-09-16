"""데모용 사용자·냉장고·구매이력·인기도 시드.

`app_user` / `user_fridge` / `user_product_affinity` / `product_popularity` 가 전부 0행이라
마이냉장고 쿼리 4종과 `reorder_candidates` 가 실제 데이터로 한 번도 안 돌았습니다.
`rag_lab` 의 추천 문구 실험도 입력이 `my_recipe_candidates` 결과인데, 냉장고가 비어 있어
그 쿼리가 빈손입니다. 그걸 푸는 것이 이 모듈입니다.

## Faker 를 쓰지 않습니다

`app_user` 에 `user_id` 와 `created_at` 뿐입니다. 이름도 이메일도 주소도 없어서 Faker 의
ko_KR 로케일이 채울 자리가 없습니다. 나머지 표도 전부 정수·날짜·기존 행의 FK 입니다.
어려운 것은 "그럴듯한 한국어 문자열" 이 아니라 **"어떤 사용자가 어떤 상품을 살 만한가"**
이고, 그건 도메인 로직이라 아래에 직접 씁니다.
자세한 검토는 `ai_context/aI가 쓴 문서/가짜 데이터 생성 도입 검토.md`.

## 두 가지 규칙

**냉장고에는 재료가 연결된 상품만 넣습니다.** `product_ingredient` 가 없는 상품을 넣으면
레시피 매칭이 되지 않아 `my_recipe_candidates` 가 계속 빈손입니다. 데모 데이터가 있는데도
화면이 비는 것만큼 헷갈리는 상태가 없습니다.

**경계 사례를 일부러 만듭니다.** 추천 문구 실험이 그걸 입력으로 씁니다.
- 냉장고가 빈 사용자 (추천할 수 없는 경우)
- 기한이 지난 재료만 가진 사용자 (보유로 치면 안 되는 경우)
- 재료가 많아 여러 레시피가 걸리는 사용자

## 재고(`product.stock_quantity`)도 여기서 채웁니다

**이 파이프라인에서 유일하게 지어내는 값입니다.** 우리는 쇼핑몰을 운영하지 않으므로
재고에는 참값이 없고, 크롤 원천에도 0% 들어 있습니다. 그런데
`reorder_candidates` 와 `missing_ingredient_products` 가 이 컬럼으로 거르기 때문에
전부 NULL 이면 "품절" 경로가 한 번도 실행되지 않습니다.

`storage_type` 이나 `brand_name` 은 지어내지 않습니다. 그쪽은 유도하거나
원천에서 꺼낼 수 있습니다. 검토는 `ai_context/aI가 쓴 문서/가짜 데이터 생성 도입 검토.md` 6절.

> **의미가 바뀝니다.** 팀 정규화 가이드 4.3 이 `stock_quantity` NULL 을 "수량 미상"
> 으로 정의하고, 쿼리가 `IS NULL OR > 0` 으로 판매 가능하게 봅니다. 숫자를 채우면
> 그 분기가 더는 안 타고 0 인 상품이 실제로 빠집니다. 그게 이 작업의 목적입니다.

## 재현 가능합니다

난수 시드를 고정합니다. 같은 `--users` 로 다시 돌리면 같은 데이터가 나옵니다.
사용자 id 는 시드 픽스처와 같은 90억 대역이라 실제 데이터와 섞이지 않고, 다시 돌릴 때
그 대역만 지우면 됩니다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg

from data_pipeline.config import Settings, get_settings
from data_pipeline.load.bulk_insert import load_connection_scope

# 시드 픽스처(`recsys_sql.fixtures`)와 같은 대역. 실제 데이터와 섞이지 않습니다.
DEMO_USER_BASE = 9_200_000_000

# 데모 대역의 **위쪽 경계**. 삭제 조건을 `>= BASE` 로만 두면 열린 구간이라,
# 실제 사용자 id 가 이 위로 올라오는 날 그 사용자의 냉장고·구매이력까지 지웁니다.
# 저장소에 production DDL 이 없어(`0001_baseline`) "실제 id 는 항상 더 작다" 를
# 확인할 방법이 없으므로, 우리가 쓸 만큼만 닫아 둡니다.
DEMO_USER_MAX = DEMO_USER_BASE + 1_000_000

# 난수 시드. 바꾸지 마세요. 바꾸면 같은 명령이 다른 데이터를 만듭니다.
RANDOM_SEED = 20260915

# 사용자 한 명의 냉장고에 들어갈 상품 수 범위.
FRIDGE_MIN, FRIDGE_MAX = 3, 9

# 구매 이력 상품 수 범위.
AFFINITY_MIN, AFFINITY_MAX = 5, 15

# 인기도를 매길 상위 상품 수. 전체에 매기면 의미가 없어 롱테일 앞부분만 만듭니다.
POPULARITY_PRODUCTS = 300

# 재고 분포. 합이 1.0 이어야 합니다.
#
# 품절을 일부러 넣습니다. 안 넣으면 `stock_quantity > 0` 조건이 늘 참이라
# 그 분기가 테스트되지 않습니다. 반대로 많이 넣으면 데모 화면이 비어 보입니다.
# 3% 면 2,553 상품 중 70~80 개 정도입니다.
STOCK_SOLD_OUT_RATIO = 0.03
STOCK_LOW_RATIO = 0.12
STOCK_LOW_RANGE = (1, 9)
STOCK_NORMAL_RANGE = (10, 400)

# 우리가 쓴 재고임을 표시하는 값. `product.metadata->>'stock_source'` 에 들어갑니다.
# 이 표식이 없는 행은 남이 채운 값으로 보고 건드리지 않습니다.
STOCK_SOURCE = "seed-demo"

# 재고용 난수 스트림을 따로 씁니다. 같은 스트림을 쓰면 `--users` 를 바꿀 때마다
# 상품 재고까지 통째로 달라져, 사용자 수만 늘렸는데 품절 상품이 바뀝니다.
STOCK_SEED = RANDOM_SEED + 1


@dataclass(slots=True)
class DemoReport:
    """무엇을 얼마나 넣었는지."""

    users: int = 0
    fridge_rows: int = 0
    affinity_rows: int = 0
    popularity_rows: int = 0
    stock_rows: int = 0
    stock_sold_out: int = 0
    stock_applied: int = 0
    edge_cases: dict[str, int] = field(default_factory=dict)
    applied: bool = False

    def render(self) -> str:
        """사람이 읽을 요약."""
        lines = [
            f"사용자        {self.users}명",
            f"냉장고        {self.fridge_rows}행",
            f"구매이력      {self.affinity_rows}행",
            f"인기도        {self.popularity_rows}행",
            f"재고          {self.stock_rows}행 중 {self.stock_applied}행 반영 (품절 {self.stock_sold_out}개)",
        ]
        if self.edge_cases:
            detail = ", ".join(f"{name} {count}명" for name, count in sorted(self.edge_cases.items()))
            lines.append(f"경계 사례     {detail}")
        lines.append("DB 반영: " + ("완료" if self.applied else "안 함(dry-run)"))
        return "\n".join(lines)


async def fetch_fridge_candidates(conn: asyncpg.Connection) -> list[tuple[int, int]]:
    """(상품 id, 재료 id) 목록. **재료가 연결된 상품만** 돌려줍니다.

    `user_fridge` 의 PK 가 (ingredient_id, user_id, product_id) 라 재료 id 가 필요합니다.
    PR #10 이 그 컬럼을 지우면 이 함수도 단순해집니다.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (p.product_id) p.product_id, pi.ingredient_id
        FROM product p
        JOIN product_ingredient pi ON pi.product_id = p.product_id AND pi.role = 'PRIMARY'
        WHERE p.is_active
        ORDER BY p.product_id, pi.ingredient_id
        """
    )
    return [(row["product_id"], row["ingredient_id"]) for row in rows]


def build_rows(
    candidates: list[tuple[int, int]],
    *,
    users: int,
    now: datetime,
) -> tuple[list[tuple[int, datetime]], list[tuple], list[tuple], list[tuple], dict[str, int]]:
    """시드 행을 만듭니다. DB 를 건드리지 않아 테스트가 쉽습니다."""
    rng = random.Random(RANDOM_SEED)
    user_ids = [DEMO_USER_BASE + index for index in range(1, users + 1)]

    app_users = [(user_id, now - timedelta(days=rng.randint(1, 365))) for user_id in user_ids]

    fridge: list[tuple] = []
    affinity: list[tuple] = []
    edge_cases: dict[str, int] = {"빈 냉장고": 0, "만료만 보유": 0}

    for index, user_id in enumerate(user_ids):
        # 경계 사례를 앞쪽 두 명에게 고정으로 배정합니다. 난수에 맡기면 사용자 수가
        # 적을 때 안 나올 수 있고, 실험 입력이 매번 달라집니다.
        if index == 0:
            edge_cases["빈 냉장고"] += 1
            picks: list[tuple[int, int]] = []
            expired_only = False
        elif index == 1:
            picks = rng.sample(candidates, k=3)
            expired_only = True
            edge_cases["만료만 보유"] += 1
        else:
            picks = rng.sample(candidates, k=rng.randint(FRIDGE_MIN, FRIDGE_MAX))
            expired_only = False

        for product_id, ingredient_id in picks:
            if expired_only:
                expires = now - timedelta(days=rng.randint(1, 10))
            else:
                # 일부러 섞습니다. 만료 재료를 보유로 치지 않는지 확인하는 테스트가 있습니다.
                expires = now + timedelta(days=rng.randint(-5, 20))
            fridge.append((ingredient_id, user_id, product_id, round(rng.uniform(0.5, 3.0), 2), "개", expires))

        for product_id, _ in rng.sample(candidates, k=rng.randint(AFFINITY_MIN, AFFINITY_MAX)):
            purchase_count = rng.randint(1, 12)
            affinity.append(
                (
                    user_id,
                    product_id,
                    purchase_count,
                    # 구매 시점을 흩뿌립니다. `reorder_candidates` 가 "마지막 구매 후 N일"
                    # 로 거르는데, 전부 최근이면 결과가 항상 비어 검증이 안 됩니다.
                    now - timedelta(days=rng.randint(1, 120)),
                    round(min(1.0, purchase_count / 12 + rng.uniform(-0.1, 0.1)), 3),
                )
            )

    # 인기도는 롱테일로. 상위 소수가 대부분을 차지해야 정렬이 의미를 갖습니다.
    period_end = now.date()
    period_start = period_end - timedelta(days=7)
    popularity: list[tuple] = []
    for rank, (product_id, _) in enumerate(rng.sample(candidates, k=min(POPULARITY_PRODUCTS, len(candidates)))):
        weight = 1.0 / (rank + 1) ** 0.8
        order_count = max(1, int(weight * 500))
        popularity.append(
            (
                period_start,
                period_end,
                product_id,
                order_count,
                order_count * rng.randint(1, 3),
                int(order_count * rng.uniform(1.2, 2.5)),
                int(order_count * rng.uniform(8, 25)),
                round(weight * 1000, 3),
            )
        )

    return app_users, fridge, affinity, popularity, edge_cases


def build_stock_rows(product_ids: list[int]) -> tuple[list[tuple[int, int]], int]:
    """(상품 id, 재고 수량) 목록과 품절 개수. DB 를 건드리지 않아 테스트가 쉽습니다.

    `product_ids` 를 정렬해 두고 고정 시드를 쓰므로, 같은 상품 집합이면 몇 번을 돌려도
    같은 상품이 품절이 됩니다. 시연 중에 품절 상품이 바뀌면 설명하기 어렵습니다.
    """
    rng = random.Random(STOCK_SEED)
    rows: list[tuple[int, int]] = []
    sold_out = 0

    for product_id in sorted(product_ids):
        draw = rng.random()
        if draw < STOCK_SOLD_OUT_RATIO:
            quantity = 0
            sold_out += 1
        elif draw < STOCK_SOLD_OUT_RATIO + STOCK_LOW_RATIO:
            quantity = rng.randint(*STOCK_LOW_RANGE)
        else:
            quantity = rng.randint(*STOCK_NORMAL_RANGE)
        rows.append((product_id, quantity))

    return rows, sold_out


async def apply_stock(conn: asyncpg.Connection, rows: list[tuple[int, int]], *, reset: bool = False) -> int:
    """재고를 한 문장으로 반영하고, 실제로 바뀐 행수를 돌려줍니다.

    2,500 행에 UPDATE 를 한 번씩 보내면 Neon 왕복이 2,500 번입니다. 배열 두 개로
    묶어 보내면 한 번입니다.

    ## 남의 재고를 덮지 않습니다

    기본값은 **비어 있거나(NULL) 우리가 쓴 행**만 갱신합니다. 이 명령이 데모용 DB 를
    가리킨다는 보장이 코드에 없어서(폴더별 `.env` 뿐입니다), 조건 없이 전부 덮으면
    운영 DB 를 가리킨 순간 카탈로그 전체의 재고가 합성값으로 날아갑니다.

    NULL 은 "수량 미상" 이라 덮어도 잃을 것이 없고, `metadata.stock_source` 가 우리
    표식인 행은 우리가 지난번에 쓴 값입니다. 그 둘만 건드립니다.

    `reset=True` 는 **그 보호를 끄는 명시적 선택**입니다. 표식 없이 이미 채워진 값을
    우리 것으로 가져올 때만 쓰세요.
    """
    if not rows:
        return 0
    result = await conn.execute(
        """
        UPDATE product p
        SET stock_quantity = v.quantity,
            metadata = COALESCE(p.metadata, '{}'::jsonb) || jsonb_build_object('stock_source', $3::text),
            updated_at = NOW()
        FROM UNNEST($1::bigint[], $2::int[]) AS v(product_id, quantity)
        WHERE p.product_id = v.product_id
          AND ($4::boolean OR p.stock_quantity IS NULL OR p.metadata ->> 'stock_source' = $3::text)
        """,
        [product_id for product_id, _ in rows],
        [quantity for _, quantity in rows],
        STOCK_SOURCE,
        reset,
    )
    # asyncpg 는 "UPDATE <n>" 을 돌려줍니다.
    return int(result.rsplit(" ", 1)[-1] or 0)


async def run_demo_seed(
    *,
    users: int = 20,
    settings: Settings | None = None,
    dry_run: bool = False,
    reset_stock: bool = False,
) -> DemoReport:
    """데모 데이터를 만들고 DB 에 넣습니다.

    같은 대역의 기존 데모 행을 먼저 지우므로 몇 번을 돌려도 결과가 같습니다.
    실제 사용자 데이터는 대역이 달라 건드리지 않습니다.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    report = DemoReport()

    async with load_connection_scope(settings) as conn:
        candidates = await fetch_fridge_candidates(conn)
        if not candidates:
            raise RuntimeError("재료가 연결된 상품이 없습니다. 먼저 `load-catalog --apply` 로 상품을 적재하세요.")

        app_users, fridge, affinity, popularity, edge_cases = build_rows(candidates, users=users, now=now)

        # 재고는 냉장고 후보가 아니라 **전체 상품**에 매깁니다. 재료가 안 붙은 295개도
        # 상품 상세와 검색에는 나오므로, 거기만 NULL 로 남으면 화면이 갈립니다.
        all_product_ids = [row["product_id"] for row in await conn.fetch("SELECT product_id FROM product")]
        stock, sold_out = build_stock_rows(all_product_ids)

        report.users = len(app_users)
        report.fridge_rows = len(fridge)
        report.affinity_rows = len(affinity)
        report.popularity_rows = len(popularity)
        report.stock_rows = len(stock)
        report.stock_sold_out = sold_out
        report.edge_cases = edge_cases

        if dry_run:
            return report

        # 자식부터 지웁니다. FK 가 걸려 있습니다.
        # **닫힌 구간입니다.** `>= BASE` 만 쓰면 대역 위쪽이 열려 있어, 실제 사용자 id 가
        # 그 위로 올라오면 남의 데이터를 지웁니다.
        for table in ("user_fridge", "user_product_affinity", "app_user"):
            await conn.execute(f"DELETE FROM {table} WHERE user_id BETWEEN $1 AND $2", DEMO_USER_BASE, DEMO_USER_MAX)
        # 인기도는 사용자와 무관합니다. 같은 기간 것만 갈아끼웁니다.
        await conn.execute(
            "DELETE FROM product_popularity WHERE period_start = $1 AND period_end = $2",
            popularity[0][0],
            popularity[0][1],
        )

        await conn.copy_records_to_table("app_user", records=app_users, columns=["user_id", "created_at"])
        await conn.copy_records_to_table(
            "user_fridge",
            records=fridge,
            columns=["ingredient_id", "user_id", "product_id", "quantity", "unit", "expires_at"],
        )
        await conn.copy_records_to_table(
            "user_product_affinity",
            records=affinity,
            columns=["user_id", "product_id", "purchase_count", "last_purchased_at", "affinity_score"],
        )
        await conn.copy_records_to_table(
            "product_popularity",
            records=popularity,
            columns=[
                "period_start",
                "period_end",
                "product_id",
                "order_count",
                "quantity_sold",
                "cart_count",
                "view_count",
                "popularity_score",
            ],
        )
        report.stock_applied = await apply_stock(conn, stock, reset=reset_stock)
        report.applied = True

    return report
