"""문구 **자동 검사** 규칙 검증. LLM 을 쓰지 않습니다.

`rag_lab.recommendation.checks` — 여기가 틀리면 실험 결과를 못 믿습니다. 채점기가 틀린
채로 프롬프트를 고치면 엉뚱한 방향으로 갑니다.

한국어 동음이의어(`파`/`양파`, `배`/`배가시키다`, `가지`/`네 가지`) 회귀 테스트가 여기
모여 있습니다. 소스의 예외 목록을 지우기 전에 이 파일을 먼저 보세요.
"""

from __future__ import annotations

from reco_helpers import CASES_DIR, case, failed

from rag_lab.recommendation.checks import collect_vocabulary
from rag_lab.recommendation.core import MAX_REASON_CHARS
from rag_lab.recommendation.experiment import load_recommendation_cases


def test_ingredient_outside_the_situation_is_caught() -> None:
    """입력에 없는 재료를 말하면 환각입니다. 계획 4-3 의 첫 번째 항목."""
    vocabulary = {"두부", "참기름", "소고기"}
    assert "환각_재료" in failed(case(), "소고기를 더하면 좋습니다.", vocabulary=vocabulary)


def test_ingredients_in_the_situation_pass() -> None:
    assert failed(case(), "두부는 있으니 참기름만 더하면 됩니다.", vocabulary={"두부", "참기름", "소고기"}) == []


def test_substring_of_a_known_ingredient_is_not_a_hallucination() -> None:
    """`방울토마토` 를 가진 상황에서 사전의 `토마토` 가 걸리면 안 됩니다.

    이걸 막지 않으면 재료 이름이 겹치는 케이스마다 오탐이 납니다.
    """
    situation = case(have=["방울토마토"], missing=[])
    assert failed(situation, "방울토마토로 바로 만들 수 있어요.", vocabulary={"토마토", "방울토마토"}) == []


def test_empty_vocabulary_skips_only_the_hallucination_check() -> None:
    """사전을 안 주면 환각 검사만 건너뛰고 나머지는 그대로 돌아야 합니다."""
    assert failed(case(), "참기름은 있으니 바로 됩니다.") == ["보유_뒤집힘"]


# --- 보유/부족 뒤집힘 --------------------------------------------------------


def test_saying_a_missing_ingredient_is_available_is_caught() -> None:
    assert "보유_뒤집힘" in failed(case(), "참기름이 있으니 바로 만들 수 있어요.")


def test_saying_an_owned_ingredient_is_missing_is_caught() -> None:
    assert "보유_뒤집힘" in failed(case(), "두부가 없어서 아쉽네요.")


def test_single_character_ingredient_does_not_match_inside_a_longer_word() -> None:
    """`파` 를 가졌는데 `양파가 없어요` 가 뒤집힘으로 잡히면 안 됩니다.

    마스터에 한 글자 재료가 41종 있어서 이 경계가 없으면 오탐이 쏟아집니다.
    """
    situation = case(have=["파"], missing=["양파"])
    assert failed(situation, "파는 있지만 양파가 없어요.") == []


# --- 상비재료 ---------------------------------------------------------------


def test_telling_the_user_to_buy_a_pantry_item_is_caught() -> None:
    """소금을 사라고 하면 데모에서 바로 눈에 띕니다."""
    situation = case(have=["달걀"], missing=["소금"], pantry=["소금"])
    assert "상비재료_구매유도" in failed(situation, "소금만 사면 바로 만들 수 있어요.")


def test_mentioning_a_pantry_item_without_pushing_a_purchase_passes() -> None:
    situation = case(have=["달걀"], missing=["소금"], pantry=["소금"])
    assert failed(situation, "달걀과 소금으로 바로 만들 수 있어요.") == []


# --- 조리시간 ---------------------------------------------------------------


def test_inventing_a_cook_time_is_caught() -> None:
    """COOKRCP01 레시피는 cook_time_min 이 전부 NULL 입니다. 시간은 전부 환각입니다."""
    assert "조리시간_지어냄" in failed(case(cook_time_min=None), "20분이면 완성됩니다.")


def test_stating_a_known_cook_time_passes() -> None:
    assert failed(case(cook_time_min=20), "20분이면 완성됩니다.") == []


# --- 길이와 빈 문구 ----------------------------------------------------------


def test_reason_longer_than_the_limit_is_caught() -> None:
    assert "길이초과" in failed(case(), "가" * (MAX_REASON_CHARS + 1))


def test_empty_reason_is_caught() -> None:
    """빈 문구는 다른 검사를 전부 통과합니다. 따로 잡지 않으면 만점으로 보고됩니다."""
    assert failed(case(), "   ") == ["빈_문구"]


# --- 프롬프트 조립 -----------------------------------------------------------


def test_recipe_name_is_not_scanned_for_hallucinated_ingredients() -> None:
    """`물파래콩전` 안의 `물`, `치즈토마토 가지구이` 안의 `치즈` 가 환각으로 잡혔습니다.

    첫 베이스라인의 환각 3건이 전부 이 오탐이었습니다.
    """
    situation = case(recipe="물파래콩전", have=["파"], missing=["콩(대두)"], pantry=["식초"])
    assert failed(situation, "파가 있어 물파래콩전을 즐기실 수 있습니다.", vocabulary={"물", "파", "콩(대두)"}) == []


# --- 실험 -------------------------------------------------------------------


def test_counter_word_gaji_is_not_read_as_the_vegetable() -> None:
    """부족 재료가 3개 이상이면 개수로 줄여 쓰라고 시켰더니 `네 가지` 가 나왔고,
    채소 `가지` 로 잡혀 환각으로 보고됐습니다."""
    situation = case(recipe="샐러드", have=["아보카도"], missing=["오렌지", "사워크림", "파프리카", "루꼴라"])
    reason = "아보카도는 있으니 부족한 재료 네 가지만 준비하시면 됩니다."
    assert failed(situation, reason, vocabulary={"가지", "아보카도"}) == []


def test_verb_gajida_is_not_read_as_the_vegetable() -> None:
    """`배와 시금치를 모두 가지고 계셔서` 가 채소 `가지` 로 잡혔습니다."""
    situation = case(recipe="시금치 배 미음", have=["배", "시금치"], missing=[])
    assert failed(situation, "배와 시금치를 모두 가지고 계셔서 바로 만드실 수 있습니다.", vocabulary={"가지"}) == []


def test_real_eggplant_is_still_caught() -> None:
    """위 예외 때문에 진짜 `가지` 환각을 놓치면 안 됩니다."""
    situation = case(recipe="샐러드", have=["아보카도"], missing=[])
    assert "환각_재료" in failed(situation, "가지를 곁들이면 더 좋습니다.", vocabulary={"가지"})


def test_conditional_isseumyeon_is_not_a_possession_claim() -> None:
    """`닭 육수만 있으면 즐기실 수 있습니다` 는 보유 주장이 아니라 그 반대입니다."""
    situation = case(recipe="장국", have=["시금치"], missing=["닭 육수"])
    assert failed(situation, "시금치가 있어 닭 육수만 있으면 장국을 즐기실 수 있습니다.") == []


def test_assertive_form_is_still_caught() -> None:
    """가정법을 빼 준다고 `참기름이 있으니` 까지 놓치면 안 됩니다."""
    assert "보유_뒤집힘" in failed(case(), "참기름이 있으니 바로 만들 수 있어요.")


def test_baega_is_not_read_as_the_pear() -> None:
    """`달콤함을 배가시켜` 의 `배가` 가 과일 `배` 로 잡혔습니다."""
    situation = case(recipe="구운 바나나", have=["바나나", "호두"], missing=[])
    reason = "호두의 고소함이 바나나의 달콤함을 배가시켜 오늘 간식으로 구워 보세요."
    assert failed(situation, reason, vocabulary={"배", "바나나", "호두"}) == []


def test_real_pear_is_still_caught() -> None:
    """위 예외 때문에 진짜 `배` 환각을 놓치면 안 됩니다."""
    situation = case(recipe="구운 바나나", have=["바나나"], missing=[])
    assert "환각_재료" in failed(situation, "배를 곁들이면 더 좋습니다.", vocabulary={"배"})


# --- 판정자 교차 검증 (재채점) ----------------------------------------------


def test_recipe_name_only_exempts_its_own_span() -> None:
    """레시피 이름 안의 글자를 **문구 전체에서** 면제하면 정탐까지 막힙니다.

    `치즈토마토 가지구이` 상황에서 보유하지 않은 `치즈` 를 "치즈가 있으니" 라고 말해도
    통과했습니다. 오탐을 막으려다 진짜 환각을 놓친 것입니다.
    """
    situation = case(recipe="치즈토마토 가지구이", have=["가지", "토마토"], missing=[])
    vocabulary = {"치즈", "가지", "토마토"}

    # 이름 안에 든 것은 재료 언급이 아닙니다.
    assert failed(situation, "치즈토마토 가지구이를 오늘 저녁으로 올려보세요.", vocabulary=vocabulary) == []
    # 이름 밖에서 말하면 환각입니다.
    assert "환각_재료" in failed(situation, "치즈가 있으니 바로 만드세요.", vocabulary=vocabulary)


def test_flip_check_also_ignores_the_recipe_name_span() -> None:
    """`새우 두부 계란찜` 이라는 이름 자체가 `새우 보유 주장` 으로 읽히면 안 됩니다."""
    situation = case(recipe="새우 두부 계란찜", have=["달걀"], missing=["새우"])
    assert failed(situation, "달걀이 있으니 새우 두부 계란찜에 새우만 더하세요.") == []


def test_vocabulary_comes_from_the_case_file_only() -> None:
    """마스터 1,028종을 전부 쓰면 한 글자 재료가 아무 문장에나 걸립니다."""
    cases = load_recommendation_cases(CASES_DIR / "cases.jsonl")
    vocabulary = collect_vocabulary(cases)

    assert "시금치" in vocabulary
    assert vocabulary == {name for c in cases for name in c.known_ingredients}


# --- 실험에서 드러난 오탐 (회귀 방지) ---------------------------------------


def test_pantry_item_called_available_is_not_a_flip() -> None:
    """상비 재료는 `missing` 에 있어도 집에 있다고 봅니다(프롬프트도 그렇게 지시).

    빼지 않으면 `소금이 있으니` 같은 **올바른 문구가 뒤집힘으로 잡힙니다.**
    """
    situation = case(recipe="삼색계란찜", have=["달걀"], missing=["소금"], pantry=["소금"])
    assert failed(situation, "달걀과 소금이 있으니 바로 만드실 수 있어요.") == []


def test_a_genuine_flip_is_still_caught_when_pantry_exists() -> None:
    """상비재료를 빼 준다고 진짜 뒤집힘까지 놓치면 안 됩니다."""
    situation = case(recipe="두부조림", have=["두부"], missing=["소금", "참기름"], pantry=["소금"])
    assert "보유_뒤집힘" in failed(situation, "참기름이 있으니 바로 만들 수 있어요.")
