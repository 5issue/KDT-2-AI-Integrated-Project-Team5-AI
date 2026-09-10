"""추천 SQL 검증용 테스트 데이터.

트랜잭션 안에서만 쓰고 끝나면 롤백하는 것을 전제로 합니다. PK 는 실제 데이터와 겹치지 않도록
9,100,000,000 대역을 씁니다. 그래도 각자 Neon 브랜치에서 돌리세요.

시나리오 (의도적으로 경계 케이스를 넣어 두었습니다)
- 냉장고의 두부는 유통기한이 지났습니다 -> 보유하지 않은 것으로 쳐야 합니다.
- 소금은 상비재료(is_pantry)입니다 -> 냉장고에 없어도 보유한 것으로 쳐야 합니다.
- 두부C 는 비활성, 참기름A 는 품절입니다 -> 상품 추천 후보에서 빠져야 합니다.

컬럼은 실제 Neon 스키마 기준입니다. 문서(v0.1.0)와 다른 지점이 있어 주의가 필요합니다.
- recipe_ingredient 의 재료 FK 는 ingredient_id (문서의 ingredient_id2 아님)
- ingredient/recipe/product 에 source 계열 NOT NULL 컬럼이 있음
- ingredient.aliases 와 recipe.tags 는 text[]
- recipe_product 의 PK 는 (recipe_id, ingredient_id, product_id)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

BASE = 9_100_000_000


@dataclass(frozen=True, slots=True)
class SeedIds:
    """시드 데이터의 식별자 모음."""

    user: int = BASE + 1

    kimchi: int = BASE + 11
    pork: int = BASE + 12
    tofu: int = BASE + 13
    salt: int = BASE + 14
    sesame_oil: int = BASE + 15

    tofu_a: int = BASE + 21
    tofu_b: int = BASE + 22
    tofu_c: int = BASE + 23
    sesame_a: int = BASE + 24
    kimchi_a: int = BASE + 25
    pork_a: int = BASE + 26
    sesame_b: int = BASE + 27
    sesame_c: int = BASE + 28

    kimchi_stew: int = BASE + 31
    tofu_braise: int = BASE + 32
    pork_grill: int = BASE + 33

    @property
    def products(self) -> tuple[int, ...]:
        """시드로 넣은 상품 id 전부."""
        return (
            self.tofu_a,
            self.tofu_b,
            self.tofu_c,
            self.sesame_a,
            self.kimchi_a,
            self.pork_a,
            self.sesame_b,
            self.sesame_c,
        )

    @property
    def ingredients(self) -> tuple[int, ...]:
        """시드로 넣은 재료 id 전부."""
        return (self.kimchi, self.pork, self.tofu, self.salt, self.sesame_oil)

    @property
    def recipes(self) -> tuple[int, ...]:
        """시드로 넣은 레시피 id 전부."""
        return (self.kimchi_stew, self.tofu_braise, self.pork_grill)


# source_identity_key 는 NOT NULL + UNIQUE 입니다. 실제 마스터의 K-FIND 키와 겹치지 않도록
# TEST-SEED 접두사를 씁니다.
#
# 키를 SQL 안에서 `'TEST-SEED:' || :normalized_name` 으로 만들지 않고 별도 파라미터로 넘깁니다.
# 같은 바인딩이 varchar 컬럼과 텍스트 연결에 동시에 쓰이면 asyncpg 가 타입을 정하지 못하고
# AmbiguousParameterError(text versus character varying)를 냅니다.
_INSERT_INGREDIENT = text(
    "INSERT INTO ingredient ("
    "  ingredient_id, name, normalized_name, is_raw_material, aliases, nutrition, is_pantry, source_identity_key"
    ") VALUES ("
    "  :id, :name, :normalized_name, TRUE, '{}'::text[], '{}'::jsonb, :is_pantry, :source_key"
    ")"
)
_INSERT_PRODUCT = text(
    "INSERT INTO product ("
    "  product_id, sku, name, product_type, price, stock_quantity, is_active, metadata,"
    "  source_type, source_product_id"
    ") VALUES ("
    "  :id, :sku, :name, 'INGREDIENT', :price, :stock, :is_active, '{}'::jsonb,"
    "  'TEST-SEED', :sku"
    ")"
)
_INSERT_PRODUCT_INGREDIENT = text(
    "INSERT INTO product_ingredient (product_id, ingredient_id, role) VALUES (:product_id, :ingredient_id, 'PRIMARY')"
)
_INSERT_RECIPE = text(
    "INSERT INTO recipe ("
    "  recipe_id, name, difficulty, cook_time_min, nutrition, tags, source_type, source_recipe_id"
    ") VALUES ("
    "  :id, :name, :difficulty, :cook_time_min, '{}'::jsonb, '{}'::text[], 'TEST-SEED', :source_recipe_id"
    ")"
)
_INSERT_RECIPE_INGREDIENT = text(
    "INSERT INTO recipe_ingredient (recipe_id, ingredient_id, quantity, unit, is_required) "
    "VALUES (:recipe_id, :ingredient_id, :quantity, :unit, :is_required)"
)
_INSERT_FRIDGE = text(
    "INSERT INTO user_fridge (ingredient_id, user_id, product_id, quantity, unit, expires_at) "
    "VALUES (:ingredient_id, :user_id, :product_id, 1, '개', NOW() + MAKE_INTERVAL(days => :days))"
)
_INSERT_AFFINITY = text(
    "INSERT INTO user_product_affinity (user_id, product_id, purchase_count, last_purchased_at, affinity_score) "
    "VALUES (:user_id, :product_id, :purchase_count, NOW() - MAKE_INTERVAL(days => :days_ago), :score)"
)
_INSERT_POPULARITY = text(
    "INSERT INTO product_popularity (period_start, period_end, product_id, order_count, popularity_score) "
    "VALUES (CURRENT_DATE - 7, CURRENT_DATE, :product_id, :order_count, :score)"
)


async def seed_minimal(conn: AsyncConnection) -> SeedIds:
    """추천 쿼리 검증용 최소 데이터를 넣습니다. 호출한 쪽에서 롤백해야 합니다."""
    ids = SeedIds()

    await conn.execute(text("INSERT INTO app_user (user_id) VALUES (:id)"), {"id": ids.user})

    ingredient_seeds = [
        (ids.kimchi, "배추김치", "김치", False),
        (ids.pork, "돼지고기", "돼지고기", False),
        (ids.tofu, "두부", "두부", False),
        (ids.salt, "소금", "소금", True),
        (ids.sesame_oil, "참기름", "참기름", False),
    ]
    await conn.execute(
        _INSERT_INGREDIENT,
        [
            {
                "id": ingredient_id,
                "name": name,
                "normalized_name": normalized,
                "is_pantry": is_pantry,
                "source_key": f"TEST-SEED:{normalized}",
            }
            for ingredient_id, name, normalized, is_pantry in ingredient_seeds
        ],
    )

    await conn.execute(
        _INSERT_PRODUCT,
        [
            {"id": ids.tofu_a, "sku": "T-A", "name": "두부A", "price": 3000, "stock": 10, "is_active": True},
            {"id": ids.tofu_b, "sku": "T-B", "name": "두부B", "price": 2500, "stock": 10, "is_active": True},
            {"id": ids.tofu_c, "sku": "T-C", "name": "두부C", "price": 1000, "stock": 10, "is_active": False},
            {"id": ids.sesame_a, "sku": "S-A", "name": "참기름A", "price": 5000, "stock": 0, "is_active": True},
            {"id": ids.kimchi_a, "sku": "K-A", "name": "김치A", "price": 4000, "stock": 10, "is_active": True},
            {"id": ids.pork_a, "sku": "P-A", "name": "돼지고기A", "price": 12000, "stock": 10, "is_active": True},
            {"id": ids.sesame_b, "sku": "S-B", "name": "참기름B", "price": 8000, "stock": 5, "is_active": True},
            {"id": ids.sesame_c, "sku": "S-C", "name": "참기름C", "price": 9000, "stock": 5, "is_active": True},
        ],
    )

    await conn.execute(
        _INSERT_PRODUCT_INGREDIENT,
        [
            {"product_id": ids.tofu_a, "ingredient_id": ids.tofu},
            {"product_id": ids.tofu_b, "ingredient_id": ids.tofu},
            {"product_id": ids.tofu_c, "ingredient_id": ids.tofu},
            {"product_id": ids.sesame_a, "ingredient_id": ids.sesame_oil},
            {"product_id": ids.sesame_b, "ingredient_id": ids.sesame_oil},
            {"product_id": ids.sesame_c, "ingredient_id": ids.sesame_oil},
            {"product_id": ids.kimchi_a, "ingredient_id": ids.kimchi},
            {"product_id": ids.pork_a, "ingredient_id": ids.pork},
        ],
    )

    recipe_seeds = [
        (ids.kimchi_stew, "시드 김치찌개", 20),
        (ids.tofu_braise, "시드 두부조림", 15),
        (ids.pork_grill, "시드 삼겹살구이", 30),
    ]
    await conn.execute(
        _INSERT_RECIPE,
        [
            {
                "id": recipe_id,
                "name": name,
                "difficulty": "EASY",
                "cook_time_min": cook_time,
                "source_recipe_id": str(recipe_id),
            }
            for recipe_id, name, cook_time in recipe_seeds
        ],
    )

    # (recipe_id, ingredient_id, quantity, unit, is_required)
    recipe_lines = [
        # 김치찌개: 필수 김치+돼지고기, 선택 두부
        (ids.kimchi_stew, ids.kimchi, 300, "g", True),
        (ids.kimchi_stew, ids.pork, 200, "g", True),
        (ids.kimchi_stew, ids.tofu, 150, "g", False),
        # 두부조림: 필수 두부+참기름. 두부는 냉장고에 있지만 유통기한이 지났습니다.
        (ids.tofu_braise, ids.tofu, 300, "g", True),
        (ids.tofu_braise, ids.sesame_oil, 1, "큰술", True),
        # 삼겹살구이: 필수 돼지고기+소금. 소금은 상비재료입니다.
        (ids.pork_grill, ids.pork, 400, "g", True),
        (ids.pork_grill, ids.salt, 1, "작은술", True),
    ]
    await conn.execute(
        _INSERT_RECIPE_INGREDIENT,
        [
            {
                "recipe_id": recipe_id,
                "ingredient_id": ingredient_id,
                "quantity": quantity,
                "unit": unit,
                "is_required": is_required,
            }
            for recipe_id, ingredient_id, quantity, unit, is_required in recipe_lines
        ],
    )

    # recipe_product 의 PK 는 (recipe_id, ingredient_id, product_id) 입니다.
    # "이 레시피의 두부 자리에는 두부A 를 우선 추천" 이라는 의미가 됩니다.
    await conn.execute(
        text(
            "INSERT INTO recipe_product (recipe_id, ingredient_id, product_id, recommendation_priority) "
            "VALUES (:recipe_id, :ingredient_id, :product_id, :priority)"
        ),
        {
            "recipe_id": ids.tofu_braise,
            "ingredient_id": ids.tofu,
            "product_id": ids.tofu_a,
            "priority": 5,
        },
    )

    await conn.execute(
        _INSERT_FRIDGE,
        [
            {"ingredient_id": ids.kimchi, "user_id": ids.user, "product_id": ids.kimchi_a, "days": 7},
            {"ingredient_id": ids.pork, "user_id": ids.user, "product_id": ids.pork_a, "days": 3},
            {"ingredient_id": ids.tofu, "user_id": ids.user, "product_id": ids.tofu_a, "days": -1},
        ],
    )

    await conn.execute(
        _INSERT_AFFINITY,
        [
            {"user_id": ids.user, "product_id": ids.kimchi_a, "purchase_count": 9, "days_ago": 60, "score": 0.90},
            {"user_id": ids.user, "product_id": ids.tofu_a, "purchase_count": 7, "days_ago": 45, "score": 0.80},
            {"user_id": ids.user, "product_id": ids.tofu_b, "purchase_count": 4, "days_ago": 10, "score": 0.95},
            {"user_id": ids.user, "product_id": ids.sesame_a, "purchase_count": 2, "days_ago": 90, "score": 0.70},
        ],
    )

    await conn.execute(
        _INSERT_POPULARITY,
        [
            {"product_id": ids.sesame_b, "order_count": 5, "score": 10.0},
            {"product_id": ids.sesame_c, "order_count": 90, "score": 900.0},
        ],
    )

    return ids
