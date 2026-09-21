"""데모 시나리오를 어느 환경에서도 다시 만들 수 있게 고정합니다.

`seed-demo` 는 무작위 데모 사용자 20명을 만듭니다. 이 시드는 그중 **한 사용자의 한 흐름**을
못 박습니다. 인계서 `docs/handoff/data-pipeline-demo-seed.md` 의 요구사항을 따릅니다.

```text
GET /api/v1/recommendations/my-recipes  -> 확정 레시피가 카드에 보인다
GET /api/v1/recipes/{recipe_id}/missing-products -> 부족 재료와 대표 상품이 나온다
```

대상은 내부 숫자 id 가 아니라 **원천 키**로 찾습니다(`config/recommendation_demo_recipes.csv`,
`config/recommendation_demo_products.csv`). 내부 id 는 재적재하면 바뀝니다. 설정에 적힌
이름과 실제 행의 이름이 다르면 그 자리에서 멈춥니다. 다른 레시피에 시나리오를 덮어쓰는 것이
가장 나쁜 실패라서, 찾았다는 것만으로는 진행하지 않습니다.

몇 번을 돌려도 결과가 같습니다. 냉장고는 설정에 적힌 상품 집합으로 맞추고, 대표 상품
우선순위는 자연키 기준 upsert 입니다.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from data_pipeline.config import Settings, get_settings
from data_pipeline.load.bulk_insert import load_connection_scope

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"
RECIPES_CSV = CONFIG_DIR / "recommendation_demo_recipes.csv"
PRODUCTS_CSV = CONFIG_DIR / "recommendation_demo_products.csv"

DEMO_USER_ID = 9_200_000_002
FRIDGE_EXPIRES_DAYS = 30
FRIDGE_QUANTITY = 1
FRIDGE_UNIT = "개"
# 대표 상품임을 나타내는 값입니다. 추천 정렬의 첫 기준이 이 값입니다.
REPRESENTATIVE_PRIORITY = 100

ROLE_FRIDGE = "FRIDGE"
ROLE_MISSING = "MISSING_PRIMARY"


@dataclass(frozen=True, slots=True)
class DemoRecipe:
    """설정에 적힌 확정 레시피 한 줄입니다."""

    source_type: str
    source_recipe_id: str
    expected_name: str
    refresh_cycle: int
    display_order: int
    is_purchase_flow: bool


@dataclass(frozen=True, slots=True)
class DemoProduct:
    """설정에 적힌 데모 상품 한 줄입니다."""

    role: str
    source_type: str
    source_product_id: str
    expected_name: str
    expected_ingredient: str
    expected_parent_ingredient: str | None


@dataclass
class ScenarioReport:
    """무엇을 얼마나 맞췄고 검증이 어떻게 끝났는지."""

    config_version: str = ""
    applied_at: str = ""
    recipes: int = 0
    fridge_rows: int = 0
    hierarchy_links: int = 0
    stock_fixes: int = 0
    priority_rows: int = 0
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    applied: bool = False

    @property
    def ok(self) -> bool:
        """검증을 모두 통과했는지."""
        return all(passed for _, passed, _ in self.checks)

    def render(self) -> str:
        """사람이 읽을 요약입니다. 접속 정보는 담지 않습니다."""
        lines = [
            f"설정 버전     {self.config_version}",
            f"적용 시각     {self.applied_at}",
            f"확정 레시피   {self.recipes}개",
            f"냉장고        {self.fridge_rows}행",
            f"계층 연결     {self.hierarchy_links}건",
            f"재고 보정     {self.stock_fixes}건",
            f"대표 상품     {self.priority_rows}행",
            "",
            "검증",
        ]
        for name, passed, detail in self.checks:
            mark = "통과" if passed else "실패"
            lines.append(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        lines.append("")
        lines.append("DB 반영: " + ("완료" if self.applied else "안 함(dry-run)"))
        return "\n".join(lines)


def config_version(*paths: Path) -> str:
    """설정 파일 내용의 지문입니다. 어떤 입력으로 돌렸는지 결과에 남깁니다."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def load_recipes(path: Path = RECIPES_CSV) -> list[DemoRecipe]:
    """확정 레시피 목록을 읽습니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [
            DemoRecipe(
                source_type=row["source_type"].strip(),
                source_recipe_id=row["source_recipe_id"].strip(),
                expected_name=row["expected_name"].strip(),
                refresh_cycle=int(row["refresh_cycle"]),
                display_order=int(row["display_order"]),
                is_purchase_flow=row["is_purchase_flow_ready"].strip().lower() == "true",
            )
            for row in csv.DictReader(handle)
        ]
    if not rows:
        raise ValueError(f"{path.name} 이 비었습니다.")
    return rows


def load_products(path: Path = PRODUCTS_CSV) -> list[DemoProduct]:
    """데모 상품 목록을 읽습니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [
            DemoProduct(
                role=row["role"].strip(),
                source_type=row["source_type"].strip(),
                source_product_id=row["source_product_id"].strip(),
                expected_name=row["expected_name"].strip(),
                expected_ingredient=row["expected_ingredient"].strip(),
                expected_parent_ingredient=(row["expected_parent_ingredient"] or "").strip() or None,
            )
            for row in csv.DictReader(handle)
        ]
    if not rows:
        raise ValueError(f"{path.name} 이 비었습니다.")
    return rows


def purchase_flow_recipe(recipes: list[DemoRecipe]) -> DemoRecipe:
    """구매 흐름을 시연할 레시피 하나를 고릅니다. 설정이 정확히 하나를 지정해야 합니다."""
    targets = [recipe for recipe in recipes if recipe.is_purchase_flow]
    if len(targets) != 1:
        raise ValueError(f"is_purchase_flow_ready 가 정확히 하나여야 합니다 (현재 {len(targets)}개).")
    return targets[0]


def check_display_order(recipes: list[DemoRecipe]) -> tuple[bool, str]:
    """각 노출 주기에 1, 2, 3 이 한 번씩 있는지 봅니다."""
    cycles: dict[int, list[int]] = {}
    for recipe in recipes:
        cycles.setdefault(recipe.refresh_cycle, []).append(recipe.display_order)
    broken = {cycle: sorted(orders) for cycle, orders in cycles.items() if sorted(orders) != [1, 2, 3]}
    if broken:
        return False, f"주기별 노출 순서가 1,2,3 이 아님: {broken}"
    return True, f"{len(cycles)}개 주기 x 3개"


async def resolve_recipes(conn: asyncpg.Connection, recipes: list[DemoRecipe]) -> dict[str, int]:
    """원천 키로 레시피를 찾고 이름까지 같은지 확인합니다."""
    resolved: dict[str, int] = {}
    mismatched: list[str] = []
    for recipe in recipes:
        row = await conn.fetchrow(
            "SELECT recipe_id, name FROM recipe WHERE source_type = $1 AND source_recipe_id = $2",
            recipe.source_type,
            recipe.source_recipe_id,
        )
        if row is None:
            mismatched.append(f"{recipe.source_recipe_id} 없음")
            continue
        if row["name"] != recipe.expected_name:
            mismatched.append(f"{recipe.source_recipe_id} 이름 불일치({row['name']!r})")
            continue
        resolved[recipe.source_recipe_id] = row["recipe_id"]
    if mismatched:
        raise ValueError("확정 레시피를 찾지 못했습니다: " + ", ".join(mismatched))
    return resolved


async def resolve_products(conn: asyncpg.Connection, products: list[DemoProduct]) -> dict[str, asyncpg.Record]:
    """원천 키로 상품을 찾고 이름과 PRIMARY 재료까지 확인합니다."""
    resolved: dict[str, asyncpg.Record] = {}
    problems: list[str] = []
    for product in products:
        row = await conn.fetchrow(
            """
            SELECT p.product_id, p.name, p.is_active, p.stock_quantity,
                   pi.ingredient_id, i.name AS ingredient_name, i.parent_ingredient_id
            FROM product p
            LEFT JOIN product_ingredient pi ON pi.product_id = p.product_id AND pi.role = 'PRIMARY'
            LEFT JOIN ingredient i ON i.ingredient_id = pi.ingredient_id
            WHERE p.source_type = $1 AND p.source_product_id = $2
            """,
            product.source_type,
            product.source_product_id,
        )
        if row is None:
            problems.append(f"{product.source_product_id} 없음")
            continue
        if row["name"] != product.expected_name:
            problems.append(f"{product.source_product_id} 이름 불일치({row['name']!r})")
            continue
        if row["ingredient_name"] != product.expected_ingredient:
            problems.append(f"{product.source_product_id} PRIMARY 재료 불일치({row['ingredient_name']!r})")
            continue
        resolved[product.source_product_id] = row
    if problems:
        raise ValueError("데모 상품을 찾지 못했습니다: " + ", ".join(problems))
    return resolved


async def rollback_sql(conn: asyncpg.Connection, recipe_id: int) -> str:
    """지금 상태로 되돌리는 SQL 입니다. 바꾸기 전에 만들어 둡니다."""
    fridge = await conn.fetch(
        "SELECT ingredient_id, product_id, quantity, unit, expires_at FROM user_fridge WHERE user_id = $1",
        DEMO_USER_ID,
    )
    priority = await conn.fetch(
        "SELECT ingredient_id, product_id, recommendation_priority FROM recipe_product WHERE recipe_id = $1",
        recipe_id,
    )
    lines = [
        "BEGIN;",
        f"DELETE FROM user_fridge WHERE user_id = {DEMO_USER_ID};",
    ]
    for row in fridge:
        expires = f"'{row['expires_at'].isoformat()}'" if row["expires_at"] else "NULL"
        lines.append(
            "INSERT INTO user_fridge (ingredient_id, user_id, product_id, quantity, unit, expires_at) VALUES "
            f"({row['ingredient_id']}, {DEMO_USER_ID}, {row['product_id']}, "
            f"{row['quantity']}, '{row['unit']}', {expires});"
        )
    lines.append(f"DELETE FROM recipe_product WHERE recipe_id = {recipe_id};")
    for row in priority:
        lines.append(
            "INSERT INTO recipe_product (recipe_id, ingredient_id, product_id, recommendation_priority) VALUES "
            f"({recipe_id}, {row['ingredient_id']}, {row['product_id']}, {row['recommendation_priority']});"
        )
    lines.append("COMMIT;")
    return "\n".join(lines)


async def link_hierarchy(
    conn: asyncpg.Connection, products: list[DemoProduct], resolved: dict[str, asyncpg.Record]
) -> int:
    """`목심 -> 돼지고기` 처럼 설정이 요구한 부모 관계를 보장합니다.

    이미 다른 부모가 붙어 있으면 덮어쓰지 않고 멈춥니다. 계층은 추천 결과를 바꾸므로
    말없이 바꿀 값이 아닙니다.
    """
    linked = 0
    for product in products:
        if not product.expected_parent_ingredient:
            continue
        row = resolved[product.source_product_id]
        parent_id = await conn.fetchval(
            "SELECT ingredient_id FROM ingredient WHERE name = $1", product.expected_parent_ingredient
        )
        if parent_id is None:
            raise ValueError(f"부모 재료 {product.expected_parent_ingredient!r} 가 없습니다.")
        current = row["parent_ingredient_id"]
        if current == parent_id:
            continue
        if current is not None:
            raise ValueError(
                f"{product.expected_ingredient!r} 에 이미 다른 부모({current})가 있습니다. 손으로 확인하세요."
            )
        await conn.execute(
            "UPDATE ingredient SET parent_ingredient_id = $1 WHERE ingredient_id = $2", parent_id, row["ingredient_id"]
        )
        linked += 1
    return linked


async def fix_availability(conn: asyncpg.Connection, resolved: dict[str, asyncpg.Record]) -> int:
    """데모 상품이 비활성이거나 품절이면 살려 둡니다.

    재고를 채우지는 않습니다. NULL 은 "수량 미상"이지 품절이 아니고, 조회 쿼리도 NULL 을
    그대로 통과시킵니다. 0 인 행만 NULL 로 되돌립니다.
    """
    fixed = 0
    for row in resolved.values():
        needs_active = not row["is_active"]
        needs_stock = row["stock_quantity"] == 0
        if not (needs_active or needs_stock):
            continue
        await conn.execute(
            "UPDATE product SET is_active = TRUE, "
            "stock_quantity = CASE WHEN stock_quantity = 0 THEN NULL ELSE stock_quantity END "
            "WHERE product_id = $1",
            row["product_id"],
        )
        fixed += 1
    return fixed


async def replace_fridge(conn: asyncpg.Connection, fridge_rows: list[asyncpg.Record], now: datetime) -> int:
    """데모 사용자의 냉장고를 설정에 적힌 상품 집합으로 맞춥니다.

    `user_fridge` 의 PK 가 `(ingredient_id, user_id, product_id)` 라 재료 id 가 필요합니다.
    상품의 PRIMARY 재료를 씁니다. 유효기간은 시연 시점에 만료되지 않도록 넉넉히 둡니다.
    """
    await conn.execute("DELETE FROM user_fridge WHERE user_id = $1", DEMO_USER_ID)
    expires_at = now + timedelta(days=FRIDGE_EXPIRES_DAYS)
    await conn.executemany(
        "INSERT INTO user_fridge (ingredient_id, user_id, product_id, quantity, unit, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        [
            (row["ingredient_id"], DEMO_USER_ID, row["product_id"], FRIDGE_QUANTITY, FRIDGE_UNIT, expires_at)
            for row in fridge_rows
        ],
    )
    return len(fridge_rows)


async def upsert_priority(conn: asyncpg.Connection, recipe_id: int, missing_rows: list[asyncpg.Record]) -> int:
    """부족 재료의 대표 상품에 추천 우선순위를 답니다.

    추천 정렬의 첫 기준이 이 값입니다. 레시피 id 를 SQL 에 박지 않고 여기서 데이터로 넣습니다.
    """
    await conn.executemany(
        "INSERT INTO recipe_product (recipe_id, ingredient_id, product_id, recommendation_priority) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (recipe_id, ingredient_id, product_id) DO UPDATE SET "
        "recommendation_priority = EXCLUDED.recommendation_priority",
        [(recipe_id, row["ingredient_id"], row["product_id"], REPRESENTATIVE_PRIORITY) for row in missing_rows],
    )
    return len(missing_rows)


async def held_ingredients(conn: asyncpg.Connection) -> set[int]:
    """냉장고 상품의 PRIMARY 재료와 그 부모까지 보유로 봅니다. 추천 SQL 과 같은 규칙입니다."""
    rows = await conn.fetch(
        """
        WITH RECURSIVE fridge(ingredient_id) AS (
            SELECT DISTINCT pi.ingredient_id
            FROM user_fridge uf
            JOIN product_ingredient pi ON pi.product_id = uf.product_id AND pi.role = 'PRIMARY'
            WHERE uf.user_id = $1 AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
            UNION
            SELECT i.parent_ingredient_id
            FROM fridge f
            JOIN ingredient i ON i.ingredient_id = f.ingredient_id
            WHERE i.parent_ingredient_id IS NOT NULL
        )
        SELECT ingredient_id FROM fridge
        """,
        DEMO_USER_ID,
    )
    return {row["ingredient_id"] for row in rows}


async def verify(
    conn: asyncpg.Connection,
    *,
    recipes: list[DemoRecipe],
    products: list[DemoProduct],
    recipe_ids: dict[str, int],
    target_recipe_id: int,
    fridge_rows: list[asyncpg.Record],
    missing_rows: list[asyncpg.Record],
) -> list[tuple[str, bool, str]]:
    """인계서의 검증 8항목입니다. 하나라도 어긋나면 실패로 남깁니다."""
    checks: list[tuple[str, bool, str]] = []

    user_count = await conn.fetchval("SELECT count(*) FROM app_user WHERE user_id = $1", DEMO_USER_ID)
    checks.append(("사용자와 대상 레시피가 각각 1건", user_count == 1 and target_recipe_id > 0, f"user={user_count}"))

    checks.append(
        ("확정 레시피 원천 키와 이름 일치", len(recipe_ids) == len(recipes), f"{len(recipe_ids)}/{len(recipes)}")
    )
    order_ok, order_detail = check_display_order(recipes)
    checks.append(("노출 주기별 순서가 1,2,3", order_ok, order_detail))

    expected_products = {row["product_id"] for row in fridge_rows}
    actual = {
        row["product_id"]
        for row in await conn.fetch(
            "SELECT uf.product_id FROM user_fridge uf JOIN product p USING (product_id) "
            "WHERE uf.user_id = $1 AND p.is_active",
            DEMO_USER_ID,
        )
    }
    checks.append(("냉장고 활성 상품 집합 일치", actual == expected_products, f"{sorted(actual)}"))

    held = await held_ingredients(conn)
    parents = {product.expected_parent_ingredient for product in products if product.expected_parent_ingredient}
    parent_ids = {
        row["ingredient_id"]
        for row in await conn.fetch("SELECT ingredient_id FROM ingredient WHERE name = ANY($1::text[])", list(parents))
    }
    checks.append(("자식 보유가 부모 요구에 닿음", parent_ids <= held, f"부모 {sorted(parent_ids)}"))

    required = await conn.fetch(
        "SELECT ri.ingredient_id, i.name, i.is_pantry FROM recipe_ingredient ri "
        "JOIN ingredient i USING (ingredient_id) WHERE ri.recipe_id = $1 AND ri.is_required",
        target_recipe_id,
    )
    missing_ids = {
        row["ingredient_id"] for row in required if not row["is_pantry"] and row["ingredient_id"] not in held
    }
    expected_missing = {row["ingredient_id"] for row in missing_rows}
    missing_names = sorted(row["name"] for row in required if row["ingredient_id"] in missing_ids)
    checks.append(("계층 판정 후 부족 재료가 설정과 일치", missing_ids == expected_missing, f"{missing_names}"))

    buyable = await conn.fetch(
        "SELECT pi.ingredient_id, count(*) AS n FROM product_ingredient pi JOIN product p USING (product_id) "
        "WHERE pi.role = 'PRIMARY' AND p.is_active AND (p.stock_quantity IS NULL OR p.stock_quantity > 0) "
        "AND pi.ingredient_id = ANY($1::bigint[]) GROUP BY 1",
        list(expected_missing),
    )
    covered = {row["ingredient_id"] for row in buyable if row["n"] >= 1}
    checks.append(
        (
            "부족 재료마다 구매 가능 상품 1건 이상",
            covered == expected_missing,
            f"{len(covered)}/{len(expected_missing)}",
        )
    )

    priorities = await conn.fetch(
        "SELECT product_id, recommendation_priority FROM recipe_product WHERE recipe_id = $1", target_recipe_id
    )
    by_product = {row["product_id"]: row["recommendation_priority"] for row in priorities}
    all_hundred = all(by_product.get(row["product_id"]) == REPRESENTATIVE_PRIORITY for row in missing_rows)
    checks.append(("대표 상품 우선순위가 100", all_hundred, f"{sorted(by_product.items())}"))

    return checks


async def run_demo_scenario(
    *,
    settings: Settings | None = None,
    dry_run: bool = True,
    rollback_path: Path | None = None,
) -> ScenarioReport:
    """설정대로 시나리오를 맞추고 검증합니다.

    dry-run 은 DB 를 바꾸지 않고 현재 상태로 검증만 돌립니다. 무엇이 어긋나 있는지 먼저
    보고 적용 여부를 정하라는 뜻입니다.
    """
    settings = settings or get_settings()
    recipes = load_recipes()
    products = load_products()
    target = purchase_flow_recipe(recipes)
    now = datetime.now(UTC)

    report = ScenarioReport(config_version=config_version(RECIPES_CSV, PRODUCTS_CSV), applied_at=now.isoformat())

    async with load_connection_scope(settings) as conn:
        recipe_ids = await resolve_recipes(conn, recipes)
        resolved = await resolve_products(conn, products)
        target_recipe_id = recipe_ids[target.source_recipe_id]
        fridge_rows = [resolved[p.source_product_id] for p in products if p.role == ROLE_FRIDGE]
        missing_rows = [resolved[p.source_product_id] for p in products if p.role == ROLE_MISSING]
        report.recipes = len(recipe_ids)

        if rollback_path is not None:
            rollback_path.parent.mkdir(parents=True, exist_ok=True)
            rollback_path.write_text(await rollback_sql(conn, target_recipe_id), encoding="utf-8")

        if not dry_run:
            async with conn.transaction():
                report.hierarchy_links = await link_hierarchy(conn, products, resolved)
                report.stock_fixes = await fix_availability(conn, resolved)
                await conn.execute(
                    "INSERT INTO app_user (user_id, created_at) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    DEMO_USER_ID,
                    now,
                )
                report.fridge_rows = await replace_fridge(conn, fridge_rows, now)
                report.priority_rows = await upsert_priority(conn, target_recipe_id, missing_rows)
            report.applied = True
        else:
            report.fridge_rows = len(fridge_rows)
            report.priority_rows = len(missing_rows)

        report.checks = await verify(
            conn,
            recipes=recipes,
            products=products,
            recipe_ids=recipe_ids,
            target_recipe_id=target_recipe_id,
            fridge_rows=fridge_rows,
            missing_rows=missing_rows,
        )
    return report
