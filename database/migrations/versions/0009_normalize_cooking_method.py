"""recipe.cooking_method 를 열거값으로 모음

`varchar(50)` 한 칸인데 채워진 628행의 표기가 **102종**으로 갈려 있었다.
`굽기`(284) 옆에 `오븐 굽기`(64) `오븐구이`(21) `오븐 구이`(5) `구이`(10) `그릴`(13) 이
따로 있고, `볶음, 조림, 오븐구이` 처럼 한 칸에 여럿을 넣은 것도 있었다.
LLM 이 레시피마다 독립적으로 판단해 생긴 흔들림이다.

규칙은 `data_pipeline.domain.normalize_cooking_method` 와 같은 것을 쓴다.
여러 개면 맨 앞을 대표로 보고, 같은 칸에서는 기기 표기가 기법보다 앞선다
(`에어프라이어구이` 는 굽기가 아니라 에어프라이어).

적재기도 같이 고쳤으므로 다음 적재분부터는 이 마이그레이션이 할 일이 없다.
원문은 2단계 산출물(`data/artifacts/.../records/*.jsonl`)에 그대로 남아 있어,
기준이 바뀌면 다시 적재하면 된다.

CHECK 는 걸지 않는다. 열거값을 늘릴 때마다 마이그레이션이 필요해지고,
판정은 적재기가 이미 하고 있다.

Revision ID: 0009_cooking_method
Revises: 0008_bubble_candidate_view
"""

from alembic import op

revision = "0009_cooking_method"
down_revision = "0008_bubble_candidate_view"
branch_labels = None
depends_on = None

# (찾을 문자열, 열거값). 위에서부터 먼저 걸리는 것을 쓴다.
# `domain._METHOD_KEYWORDS` 와 같은 순서다.
KEYWORDS: tuple[tuple[str, str], ...] = (
    ("에어프라이", "에어프라이어"),
    ("전자레인지", "전자레인지"),
    ("슬로우", "슬로우쿠커"),
    ("저속조리", "슬로우쿠커"),
    ("저온조리", "슬로우쿠커"),
    ("저온 조리", "슬로우쿠커"),
    ("저열", "슬로우쿠커"),
    ("압력솥", "압력솥"),
    ("튀기", "튀김"),
    ("튀김", "튀김"),
    ("찌기", "찜"),
    ("찜", "찜"),
    ("뜸", "찜"),
    ("조림", "조림"),
    ("졸이", "조림"),
    ("볶", "볶음"),
    ("부치", "부침"),
    ("부침", "부침"),
    ("팬니", "부침"),
    ("오븐", "굽기"),
    ("그릴", "굽기"),
    ("로스", "굽기"),
    ("브로일", "굽기"),
    ("베이킹", "굽기"),
    ("베이크", "굽기"),
    ("토스트", "굽기"),
    ("시어링", "굽기"),
    ("빵 기계", "굽기"),
    ("팬 프라이", "굽기"),
    ("팬프라이", "굽기"),
    ("굽기", "굽기"),
    ("구이", "굽기"),
    ("끓", "끓이기"),
    ("삶", "끓이기"),
    ("시뮬머링", "끓이기"),
    ("중탕", "끓이기"),
    ("밥 짓기", "끓이기"),
    ("밥짓기", "끓이기"),
    ("무침", "무침"),
    ("섞", "무침"),
    ("혼합", "무침"),
    ("버무", "무침"),
    ("블렌딩", "갈기"),
    ("분쇄", "갈기"),
    ("착즙", "갈기"),
    ("냉장", "비가열"),
    ("냉동", "비가열"),
    ("비굽기", "비가열"),
    ("불리", "비가열"),
    ("불림", "비가열"),
)


def upgrade() -> None:
    """조각을 앞에서부터 훑어 처음 걸리는 열거값을 쓴다.

    맨 앞 조각만 보면 안 된다. `조리, 살짝 끓이기` 처럼 앞이 빈 말이고 뒤가 진짜인 값이
    있다. 적재기(`normalize_cooking_method`)도 조각을 순서대로 훑으므로 같게 맞춘다.
    """
    arms = " ".join(f"WHEN btrim(chunk.part) LIKE '%{needle}%' THEN '{method}'" for needle, method in KEYWORDS)
    op.execute(
        f"""
        UPDATE recipe AS target
        SET cooking_method = picked.method
        FROM (
            SELECT source.recipe_id,
                   (ARRAY_AGG(found.method ORDER BY chunk.ord))[1] AS method
            FROM recipe AS source
            CROSS JOIN LATERAL unnest(
                string_to_array(
                    regexp_replace(source.cooking_method, '[/()]| 및 | 또는 ', ',', 'g'),
                    ','
                )
            ) WITH ORDINALITY AS chunk(part, ord)
            CROSS JOIN LATERAL (SELECT CASE {arms} END AS method) AS found
            WHERE source.cooking_method IS NOT NULL
              AND found.method IS NOT NULL
            GROUP BY source.recipe_id
        ) AS picked
        WHERE target.recipe_id = picked.recipe_id
          AND target.cooking_method IS DISTINCT FROM picked.method
        """
    )
    # 어디에도 안 걸린 값은 비운다. `조리`, `가열`, `팬 조리` 4행이 여기 해당한다.
    # 있으나 마나 한 값이라 화면과 필터 양쪽에서 걸리적거린다.
    allowed = ", ".join(f"'{method}'" for method in sorted({method for _, method in KEYWORDS}))
    op.execute(f"UPDATE recipe SET cooking_method = NULL WHERE cooking_method NOT IN ({allowed})")


def downgrade() -> None:
    """원문으로 되돌리지 않는다.

    102종을 14종으로 모으는 변환이라 되돌릴 정보가 남아 있지 않다. 여기서 예외를 던지면
    아래 리비전으로의 스키마 롤백까지 막히므로 아무것도 하지 않는다.
    원문이 필요하면 2단계 산출물로 다시 적재한다.
    """
