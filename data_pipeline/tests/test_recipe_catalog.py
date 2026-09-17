"""COOKRCP01 파싱 검증. DB 를 쓰지 않습니다.

재료 파싱이 틀리면 레시피가 들어가도 `my_recipe_candidates` 가 제대로 안 돕니다.
"""

from __future__ import annotations

from decimal import Decimal

from data_pipeline.load import recipe_catalog as rc


def names(raw: str) -> list[str]:
    """재료명만."""
    return [name for name, _ in rc.parse_ingredients(raw)]


def test_parses_a_plain_comma_list() -> None:
    """가장 흔한 모양입니다."""
    raw = "연두부 75g(3/4모), 칵테일새우 20g(5마리), 달걀 30g(1/2개)"

    assert names(raw) == ["연두부", "칵테일새우", "달걀"]


def test_section_titles_are_dropped() -> None:
    """`●양념장 :` 같은 소제목이 재료로 잡히면 미매칭만 늘어납니다."""
    raw = "●방울토마토 소박이 : \n방울토마토 150g(5개), 양파 10g\n●양념장 : \n고춧가루 4g, 설탕 2g"

    assert names(raw) == ["방울토마토", "양파", "고춧가루", "설탕"]


def test_portion_prefix_is_dropped() -> None:
    """`[1인분]` 이 첫 재료명에 붙어 들어갑니다."""
    assert names("[1인분]조선부추 50g, 날콩가루 7g") == ["조선부추", "날콩가루"]


def test_quantity_is_kept_as_written() -> None:
    """`75g` 를 숫자로 쪼개는 것은 부피->무게 환산과 얽혀 이번 범위 밖입니다."""
    assert rc.parse_ingredients("연두부 75g(3/4모)") == [("연두부", "75g")]
    assert rc.parse_ingredients("참깨 약간") == [("참깨", "")]


def test_state_words_are_stripped_from_the_name() -> None:
    """`참깨 약간` 의 `약간` 은 수량이 아니라 상태입니다."""
    assert names("통깨 약간, 소금 적당량") == ["통깨", "소금"]


def test_duplicates_within_one_recipe_collapse() -> None:
    """같은 재료가 양념장에도 나오면 두 번 세어집니다. ON CONFLICT 가 같은 행을 두 번 건드립니다."""
    assert names("마늘 5g, 설탕 2g\n●양념장 : 마늘 3g, 간장 5g") == ["마늘", "설탕", "간장"]


def test_prep_state_prefixes_are_candidates_not_the_name() -> None:
    """`다진 마늘` 은 `마늘` 과 같은 재료입니다. 원문을 먼저 보고 안 맞으면 뗀 형태를 봅니다."""
    assert rc.match_candidates("다진 마늘")[0] == "다진 마늘"
    assert "마늘" in rc.match_candidates("다진 마늘")
    assert "마늘" in rc.match_candidates("굵게 다진 마늘")
    assert "대파" in rc.match_candidates("어슷 썬 대파")
    assert "마늘" in rc.match_candidates("마늘 다진것")


def test_powder_is_not_stripped() -> None:
    """`고춧가루`/`마늘가루` 는 생재료와 별도 식품입니다(정규화 가이드 3.4).

    상태만 떼고 가루는 건드리지 않습니다. 규칙과 판단을 섞지 않으려는 것입니다.
    """
    assert rc.match_candidates("고춧가루") == ["고춧가루"]
    assert rc.match_candidates("마늘가루") == ["마늘가루"]
    # 후춧가루 -> 후추 는 판단이 필요해 여기서 하지 않습니다. 3단계 resolve 몫입니다.
    assert "후추" not in rc.match_candidates("후춧가루")


def test_oil_spelling_variants() -> None:
    """`올리브오일` 과 `올리브유` 는 같은 것을 다르게 적은 것뿐입니다."""
    assert "올리브유" in rc.match_candidates("올리브오일")


def test_one_letter_names_are_kept() -> None:
    """한국어 재료에는 한 음절이 흔합니다. 버리면 이 데이터에서만 648줄이 사라집니다.

    마스터 1,028종 중 41종이 한 글자입니다 — 물·꿀·무·배·쌀·파·잣·밤·김·떡.
    """
    assert names("물 200ml, 밥 210g, 무 50g") == ["물", "밥", "무"]


def test_rows_report_shows_match_rate() -> None:
    """적재 전에 얼마나 붙는지 보여야 합니다. 61% 로 들어간 것을 모르고 지나치면 안 됩니다."""
    rows = rc.RecipeRows(recipes=[()], ingredients=[(), ()], unmatched={"후춧가루": 2})

    rendered = rows.render()
    assert "2/4행 (50%)" in rendered
    assert "후춧가루 2" in rendered


def test_html_tags_are_stripped() -> None:
    """원본에 `<br>` 이 섞여 있습니다. 떼지 않으면 재료명으로 잡힙니다(실제로 21건).

    태그를 줄바꿈으로 바꿉니다. 원본에서 구획 구분자 노릇을 하고 있어서입니다.
    """
    raw = "2인분 기준<br>\n• [가정 간편식 재료] 떡(130g), 어묵(40g)<br />\n• [추가 재료] 부추(10g)"

    assert names(raw) == ["떡", "어묵", "부추"]
    assert "<br>" not in names(raw)


# --- 수량 파싱 (코드래빗 리뷰 반영) -------------------------------------------


def test_fraction_quantity_is_computed_not_concatenated() -> None:
    """예전에는 숫자 아닌 글자를 전부 지워 `1/2알` 이 `12` 로 저장됐습니다.

    COOKRCP01 5,813개 수량 표기 중 2건이 실제로 그렇게 들어갔습니다.
    """
    assert rc._quantity("1/2알") == Decimal("0.5")
    assert rc._quantity("2/3작은술") == Decimal("0.67")


def test_mixed_number_is_supported() -> None:
    assert rc._quantity("1 1/2컵") == Decimal("1.5")


def test_vulgar_fraction_symbols_are_supported() -> None:
    """원천이 `½큰술` 처럼 유니코드 기호를 씁니다."""
    assert rc._quantity("½큰술") == Decimal("0.5")
    assert rc._quantity("1¼개") == Decimal("1.25")


def test_plain_numbers_still_work() -> None:
    assert rc._quantity("75g") == Decimal("75")
    assert rc._quantity("2.5큰술") == Decimal("2.5")


def test_unparseable_quantity_is_null_not_a_guess() -> None:
    """틀린 숫자를 남기는 것보다 비우는 편이 낫습니다. 원문은 raw_text 에 남습니다."""
    assert rc._quantity("약간") is None
    assert rc._quantity("") is None
    assert rc._quantity("1/0개") is None, "0 으로 나누면 안 됩니다"
