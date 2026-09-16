"""추천 문구 검사 규칙 검증. DB 도 API 도 쓰지 않습니다.

여기가 틀리면 실험 결과를 못 믿습니다. 채점기가 틀린 채로 프롬프트를 고치면
엉뚱한 방향으로 갑니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_lab.recommendation import (
    MAX_REASON_CHARS,
    PASS_MEAN_SCORE,
    RUBRIC_ITEMS,
    RecommendationCase,
    build_situation,
    case_fingerprint,
    check_reason,
    collect_vocabulary,
    load_reasons,
    load_recommendation_cases,
    parse_rubric,
    recommendation_reason,
    rescore_experiment,
    run_reason_experiment,
    score_reason,
)
from rag_lab.testing import FakeChatClient

CASES_DIR = Path(__file__).resolve().parents[1] / "experiments" / "openLeeWorld"


def case(**overrides: object) -> RecommendationCase:
    """기본 상황 하나. 필요한 필드만 덮어씁니다."""
    payload: dict = {
        "case_id": "t",
        "recipe": "두부조림",
        "match_rate": 0.5,
        "have": ["두부"],
        "missing": ["참기름"],
        "pantry": [],
        "cook_time_min": None,
    }
    payload.update(overrides)
    return RecommendationCase(**payload)


def failed(case_: RecommendationCase, reason: str, **kwargs: object) -> list[str]:
    """실패한 검사 이름만."""
    checks = check_reason(case_, reason, **kwargs)  # type: ignore[arg-type]
    return [check.name for check in checks if not check.passed]


# --- 환각 -------------------------------------------------------------------


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


def test_missing_cook_time_is_stated_not_omitted() -> None:
    """줄을 빼면 모델이 알아서 지어냅니다. 명시적으로 없다고 적습니다."""
    assert "조리시간: (알 수 없음)" in build_situation(case(cook_time_min=None))


def test_empty_lists_are_stated_explicitly() -> None:
    assert "부족한 재료: (없음)" in build_situation(case(missing=[]))


# --- 생성과 검수 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_quotes_around_the_reason_are_stripped() -> None:
    """모델이 따옴표로 감싸는 일이 잦습니다. 화면에 그대로 나가면 안 됩니다."""
    chat = FakeChatClient('"두부는 있으니 참기름만 더하면 됩니다."')
    assert await recommendation_reason(case(), chat=chat) == "두부는 있으니 참기름만 더하면 됩니다."


@pytest.mark.asyncio
async def test_situation_is_wrapped_as_data() -> None:
    """상황은 데이터입니다. 태그로 감싸지 않으면 note 의 문장이 지시문으로 읽힙니다."""
    chat = FakeChatClient("문구")
    await recommendation_reason(case(), chat=chat)
    assert chat.last_user is not None
    assert chat.last_user.startswith("<situation>")


def test_recipe_name_is_not_scanned_for_hallucinated_ingredients() -> None:
    """`물파래콩전` 안의 `물`, `치즈토마토 가지구이` 안의 `치즈` 가 환각으로 잡혔습니다.

    첫 베이스라인의 환각 3건이 전부 이 오탐이었습니다.
    """
    situation = case(recipe="물파래콩전", have=["파"], missing=["콩(대두)"], pantry=["식초"])
    assert failed(situation, "파가 있어 물파래콩전을 즐기실 수 있습니다.", vocabulary={"물", "파", "콩(대두)"}) == []


# --- 실험 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_reasons_lower_the_distinct_ratio() -> None:
    """20~30개 문구가 전부 같으면 틀에 박힌 문장을 찍어내고 있는 것입니다(계획 4-3)."""
    cases = [case(case_id="a"), case(case_id="b"), case(case_id="c")]
    report = await run_reason_experiment("t", cases, chat=FakeChatClient("같은 문구입니다."))

    assert report.distinct_ratio == pytest.approx(1 / 3)
    assert len(report.results) == 3


@pytest.mark.asyncio
async def test_report_counts_which_check_failed(tmp_path: Path) -> None:
    cases = [case(case_id="a"), case(case_id="b")]
    report = await run_reason_experiment("t", cases, chat=FakeChatClient("참기름이 있어서 20분이면 됩니다."))

    assert report.check_pass_rate == 0.0
    assert report.failure_counts == {"보유_뒤집힘": 2, "조리시간_지어냄": 2}

    # 결과 파일 첫 줄은 메타데이터. 질문 세트 실험과 같은 규칙입니다.
    path = report.write_jsonl(tmp_path)
    meta = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert meta["kind"] == "recommendation_reason"
    assert "api_key" not in json.dumps(meta).lower(), "자격증명이 결과에 남으면 안 됩니다"


@pytest.mark.asyncio
async def test_judge_is_skipped_unless_requested() -> None:
    """채점은 케이스당 LLM 호출이 한 번 더 늘어납니다. 기본값은 안 부르는 것입니다."""
    report = await run_reason_experiment("t", [case()], chat=FakeChatClient("문구"))
    assert report.judge_pass_rate is None
    assert report.results[0].rubric is None


# --- 상황 세트 파일 ----------------------------------------------------------


def test_case_file_loads_and_covers_the_required_edges() -> None:
    """계획 4-2 가 요구한 경계 사례가 실제로 파일에 있는지 봅니다."""
    cases = load_recommendation_cases(CASES_DIR / "cases.jsonl")

    assert 20 <= len(cases) <= 30, "20~30개면 충분합니다(계획 4-2)"
    assert any(not c.missing for c in cases), "부족 재료 0개"
    assert any(len(c.missing) == 1 for c in cases), "부족 재료 1개"
    assert any(len(c.missing) >= 3 for c in cases), "부족 재료 3개 이상"
    assert any(c.missing and set(c.missing) <= set(c.pantry) for c in cases), "상비재료만 부족"
    assert any(c.cook_time_min is None for c in cases), "조리시간 없음"
    assert any(c.cook_time_min is not None for c in cases), "조리시간 있음"
    assert any(not c.have for c in cases), "빈 냉장고"

    # 같은 재료가 여러 레시피에 걸리는 묶음. 문구가 전부 같아지는지 보는 용도입니다.
    with_spinach = [c for c in cases if "시금치" in c.known_ingredients]
    assert len({c.recipe for c in with_spinach}) >= 3


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    """id 가 겹치면 결과에서 어느 케이스였는지 구분이 안 됩니다."""
    path = tmp_path / "cases.jsonl"
    line = json.dumps({"case_id": "dup", "recipe": "가", "match_rate": 1.0}, ensure_ascii=False)
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="중복"):
        load_recommendation_cases(path)


def test_vocabulary_comes_from_the_case_file_only() -> None:
    """마스터 1,028종을 전부 쓰면 한 글자 재료가 아무 문장에나 걸립니다."""
    cases = load_recommendation_cases(CASES_DIR / "cases.jsonl")
    vocabulary = collect_vocabulary(cases)

    assert "시금치" in vocabulary
    assert vocabulary == {name for c in cases for name in c.known_ingredients}


# --- 실험에서 드러난 오탐 (회귀 방지) ---------------------------------------


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


# --- 루브릭 채점 -------------------------------------------------------------
#
# 항목은 `ai_context/사람이 쓴 문서/rag 평가 지표 종류.md` 7절을 옮긴 것입니다.


def rubric_json(default: int = 8, **overrides: int) -> str:
    """판정자 응답 하나. 지정하지 않은 항목은 `default` 점."""
    scores = {key: overrides.get(key, default) for key, _, _ in RUBRIC_ITEMS}
    return json.dumps({**scores, "comment": "사유"}, ensure_ascii=False)


def test_rubric_has_the_eight_items_from_the_team_document() -> None:
    """항목이 바뀌면 지난 실험과 점수를 비교할 수 없습니다. 목록을 고정합니다."""
    keys = [key for key, _, _ in RUBRIC_ITEMS]
    assert keys == [
        "goal",
        "brand",
        "differentiation",
        "clarity",
        "attractiveness",
        "relevance",
        "compliance",
        "brevity",
    ]


def test_mean_at_the_threshold_passes() -> None:
    """통과선은 평균 7.5 입니다. 경계값이 어느 쪽인지 못박아 둡니다."""
    assert parse_rubric(rubric_json(8, goal=7)).mean == pytest.approx(7.875)
    assert parse_rubric(rubric_json(8, goal=7)).passed

    # 8개 중 넷이 10점, 넷이 5점이면 합 60 / 8 = 7.5 입니다.
    borderline = parse_rubric(rubric_json(5, goal=10, brand=10, differentiation=10, clarity=10))
    assert borderline.mean == pytest.approx(PASS_MEAN_SCORE)
    assert borderline.passed, "정확히 7.5 는 통과입니다"


def test_mean_below_the_threshold_fails() -> None:
    assert not parse_rubric(rubric_json(7)).passed


def test_weakest_item_points_at_what_to_fix() -> None:
    """총점만 보면 프롬프트를 어디로 고칠지 모릅니다. 쪼갠 이유가 이것입니다."""
    assert parse_rubric(rubric_json(9, compliance=2)).weakest == "compliance"


def test_scores_out_of_range_are_clamped() -> None:
    """12점을 그대로 받으면 평균이 부풀어 통과선이 의미를 잃습니다."""
    score = parse_rubric(rubric_json(8, goal=12, brand=-3))
    assert score.scores["goal"] == 10
    assert score.scores["brand"] == 0


def test_code_fence_around_the_json_is_tolerated() -> None:
    """모델이 ```json 으로 감싸는 일이 잦습니다."""
    assert parse_rubric(f"```json\n{rubric_json()}\n```").passed


def test_unparseable_response_is_not_counted_as_a_quality_failure() -> None:
    """파싱 실패는 판정자 문제입니다. 품질 실패로 세면 프롬프트를 고쳐도
    숫자가 안 움직이는 이유를 못 찾습니다."""
    for raw in ("응답 없음", "{", '{"goal": "높음"}', "[]"):
        score = parse_rubric(raw)
        assert score.error, raw
        assert score.mean is None
        assert score.passed is None


def test_missing_item_is_a_scoring_failure() -> None:
    """항목 하나가 빠진 채 평균을 내면 그 항목을 0점으로도 10점으로도 세게 됩니다."""
    partial = json.dumps({"goal": 9, "comment": "사유"}, ensure_ascii=False)
    assert parse_rubric(partial).error


@pytest.mark.asyncio
async def test_situation_and_reason_both_reach_the_judge() -> None:
    """상황 없이 문구만 주면 목적성·공감성을 잴 수 없습니다."""
    judge = FakeChatClient(rubric_json())
    await score_reason(case(), "두부는 있으니 참기름만 더하면 됩니다.", chat=judge)

    assert judge.last_user is not None
    assert "<situation>" in judge.last_user
    assert "<reason>" in judge.last_user


@pytest.mark.asyncio
async def test_report_separates_scoring_failures_from_low_scores() -> None:
    report = await run_reason_experiment(
        "t",
        [case(case_id="a"), case(case_id="b")],
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient("판정 불가"),
    )

    assert report.unscored == 2
    assert report.mean_score is None
    assert report.judge_pass_rate is None, "채점 실패를 불합격으로 세면 안 됩니다"


@pytest.mark.asyncio
async def test_report_carries_item_means(tmp_path: Path) -> None:
    """어느 축이 약한지가 결과 파일에 남아야 나중에 비교할 수 있습니다."""
    report = await run_reason_experiment(
        "t",
        [case(case_id="a")],
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient(rubric_json(9, compliance=3)),
    )

    assert report.item_means["compliance"] == pytest.approx(3.0)
    meta = json.loads(report.write_jsonl(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert meta["item_means"]["compliance"] == 3.0
    assert meta["params"]["pass_mean_score"] == PASS_MEAN_SCORE


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


@pytest.mark.asyncio
async def test_rescore_does_not_regenerate(tmp_path: Path) -> None:
    """판정자를 비교하려면 **같은 문구**를 다시 재야 합니다.

    새로 생성하면 문구 차이와 판정자 차이가 섞여 무엇 때문에 점수가 바뀌었는지
    알 수 없습니다.
    """
    cases = [case(case_id="a"), case(case_id="b")]
    first = await run_reason_experiment(
        "first",
        cases,
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient(rubric_json()),
    )
    path = first.write_jsonl(tmp_path)

    judge = FakeChatClient(rubric_json(6))
    again = await rescore_experiment("again", cases, load_reasons(path), judge=judge)

    assert [r.reason for r in again.results] == [r.reason for r in first.results]
    assert again.mean_score == pytest.approx(6.0)
    assert again.params["rescored"] is True


def test_load_reasons_skips_the_metadata_line(tmp_path: Path) -> None:
    """결과 파일 첫 줄은 메타데이터입니다. 케이스로 세면 개수가 하나 늘어납니다."""
    path = tmp_path / "r.jsonl"
    meta = json.dumps({"name": "x", "params": {}}, ensure_ascii=False)
    row = json.dumps({"case_id": "a", "reason": "문구"}, ensure_ascii=False)
    path.write_text(f"{meta}\n{row}\n", encoding="utf-8")

    prior = load_reasons(path)
    assert set(prior) == {"a"}
    assert prior["a"].reason == "문구"


@pytest.mark.asyncio
async def test_rescore_rejects_a_case_missing_from_the_previous_run(tmp_path: Path) -> None:
    """상황 세트를 고친 뒤 옛 결과로 재채점하면 조용히 일부만 채점됩니다."""
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"case_id": "a", "reason": "문구"}, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="지난 결과에 없는"):
        await rescore_experiment(
            "x", [case(case_id="a"), case(case_id="b")], load_reasons(path), judge=FakeChatClient(rubric_json())
        )


# --- 코드래빗 리뷰 반영 (#14) ------------------------------------------------


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


def test_case_fingerprint_changes_with_content() -> None:
    """id 를 그대로 둔 채 내용을 고치면 지문이 달라져야 합니다."""
    base = case()
    assert case_fingerprint(base) == case_fingerprint(case())
    assert case_fingerprint(base) != case_fingerprint(case(missing=["참기름", "간장"]))
    assert case_fingerprint(base) != case_fingerprint(case(cook_time_min=20))
    assert case_fingerprint(base) != case_fingerprint(case(recipe="다른레시피"))


def test_ingredient_order_does_not_change_the_fingerprint() -> None:
    """목록 순서는 의미가 없습니다. 순서만 바뀌었다고 재채점을 막으면 안 됩니다."""
    assert case_fingerprint(case(have=["두부", "달걀"])) == case_fingerprint(case(have=["달걀", "두부"]))


@pytest.mark.asyncio
async def test_rescore_rejects_a_case_whose_content_changed(tmp_path: Path) -> None:
    """id 가 같아도 상황이 바뀌었으면 옛 문구를 새 상황으로 채점하게 됩니다."""
    original = [case(case_id="a")]
    first = await run_reason_experiment(
        "first",
        original,
        chat=FakeChatClient("두부가 있어 맛있게 즐기실 수 있습니다."),
        judge=FakeChatClient(rubric_json()),
    )
    path = first.write_jsonl(tmp_path)

    edited = [case(case_id="a", missing=["참기름", "간장", "설탕"])]
    with pytest.raises(ValueError, match="상황이 바뀐"):
        await rescore_experiment("again", edited, load_reasons(path), judge=FakeChatClient(rubric_json()))


@pytest.mark.asyncio
async def test_rescore_records_whether_it_could_verify(tmp_path: Path) -> None:
    """옛 결과 파일에는 지문이 없습니다. 확인 못 했다는 사실을 기록에 남깁니다."""
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"case_id": "a", "reason": "옛 문구"}, ensure_ascii=False) + "\n", encoding="utf-8")

    report = await rescore_experiment("x", [case(case_id="a")], load_reasons(path), judge=FakeChatClient(rubric_json()))
    assert report.params["case_hash_verified"] is False
