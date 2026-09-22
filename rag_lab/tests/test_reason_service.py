"""``rag_lab.reason_service`` 검증. LLM·DB 없이 돕니다.

OpenRouter 는 ``httpx.MockTransport`` 로 흉내 냅니다. 여기서 보는 것은 결정 사항 그대로입니다.
카드마다 1회 호출, 동시에 생성, 카드별 제한 시간, 검사 실패는 그 카드만 템플릿으로.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from rag_lab.reason_service import (
    OpenRouterReasonClient,
    ReasonSettings,
    RecipeFacts,
    check_reason,
    facts_from_row,
    generate_reasons_for_rows,
    template_reason,
)
from rag_lab.reason_service.checks import failed_names
from rag_lab.reason_service.facts import validate_facts

FRIDGE_ROWS = [
    {
        "recipe_id": 137,
        "name": "된장 두부찌개",
        "cook_time_min": None,
        "required_count": 7,
        "available_count": 6,
        "missing_count": 1,
        "match_rate": "0.857",
        "held_ingredients": json.dumps(
            [{"ingredient_id": 1, "name": n} for n in ("두부", "대파", "돼지고기", "배추김치")]
        ),
        "pantry_ingredients": json.dumps([{"ingredient_id": 2, "name": n} for n in ("된장", "고춧가루")]),
        "missing_ingredients": json.dumps([{"ingredient_id": 3, "name": "고추"}]),
    },
    {
        "recipe_id": 3277,
        "name": "치즈토마토 가지구이",
        "cook_time_min": None,
        "required_count": 7,
        "available_count": 5,
        "missing_count": 2,
        "match_rate": "0.714",
        "held_ingredients": [{"name": n} for n in ("가지", "토마토", "양파", "목심")],
        "pantry_ingredients": [{"name": "올리브유"}],
        "missing_ingredients": [{"name": "새송이버섯"}, {"name": "모차렐라치즈"}],
    },
    {
        "recipe_id": 250,
        "name": "단호박제육볶음",
        "cook_time_min": None,
        "required_count": 9,
        "available_count": 6,
        "missing_count": 3,
        "match_rate": "0.667",
        "held_ingredients": [{"name": n} for n in ("대파", "양파", "돼지고기")],
        "pantry_ingredients": [{"name": n} for n in ("고추장", "간장", "참기름")],
        "missing_ingredients": [{"name": n} for n in ("단호박", "마늘", "고추")],
    },
]

GOOD_REASONS = {
    "된장 두부찌개": (
        "담백한 두부와 묵직한 돼지고기, 시원한 배추김치가 어우러져 깊은 맛을 냅니다. "
        "고추를 더하면 칼칼한 국물 맛을 살릴 수 있습니다."
    ),
    "치즈토마토 가지구이": (
        "수분이 많은 가지와 상큼한 토마토가 부드럽게 구워지기 좋은 상태입니다. "
        "모차렐라치즈와 새송이버섯을 채우면 풍성한 요리가 됩니다."
    ),
    "단호박제육볶음": (
        "담백한 돼지고기에 아삭한 양파와 대파를 더해 볶기 알맞습니다. "
        "단호박과 마늘, 고추를 곁들이면 다채로운 식감을 즐길 수 있습니다."
    ),
}


def _recipe_of(request: httpx.Request) -> str:
    user = json.loads(request.content)["messages"][1]["content"]
    return user.split("레시피: ", 1)[1].split("\n", 1)[0]


def _chat_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": text}}]})


def _client(handler: Any, timeout: float = 2.0) -> OpenRouterReasonClient:
    """동기·비동기 handler 를 모두 받습니다. MockTransport 가 둘 다 지원합니다."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterReasonClient(http, ReasonSettings(api_key="test-key", timeout_seconds=timeout))


# --- facts ------------------------------------------------------------------


def test_facts_from_row_parses_jsonb_string_and_list() -> None:
    facts = facts_from_row(FRIDGE_ROWS[0])
    assert facts.have == ["두부", "대파", "돼지고기", "배추김치"]
    assert facts.pantry == ["된장", "고춧가루"]
    assert facts.effective_missing == ["고추"]
    assert facts_from_row(FRIDGE_ROWS[1]).missing == ["새송이버섯", "모차렐라치즈"]


def test_facts_from_row_tolerates_missing_list_columns() -> None:
    """옛 행 모양(held/pantry 없음)도 count 없이 옮겨집니다."""
    facts = facts_from_row({"name": "김치찌개", "missing_ingredients": "[]"})
    assert facts.have == [] and facts.missing == [] and facts.required_count is None


def test_facts_from_row_rejects_inconsistent_counts() -> None:
    row = dict(FRIDGE_ROWS[0], missing_count=2)
    with pytest.raises(ValueError):
        validate_facts(facts_from_row(row))


# --- template -----------------------------------------------------------------


def test_template_covers_four_branches_and_particles() -> None:
    base = RecipeFacts(recipe="된장 두부찌개", have=["두부"], pantry=["소금"])
    assert template_reason(base) == "된장 두부찌개를 바로 만들어 보세요."
    assert template_reason(RecipeFacts(recipe="김치찜", have=["김치"], missing=["돼지고기"])) == (
        "김치를 활용하고 돼지고기만 더하면 김치찜을 만들 수 있어요."
    )
    assert "고추·버섯 등 3가지를 더 준비하면" in template_reason(
        RecipeFacts(recipe="두부조림", missing=["고추", "버섯", "애호박"])
    )
    assert template_reason(RecipeFacts(recipe="잡채", missing=["당면", "시금치", "당근", "양파"])) == (
        "잡채에 필요한 재료 4가지를 장보기 목록에서 확인해 보세요."
    )


def test_template_ignores_pantry_when_counting_missing() -> None:
    facts = RecipeFacts(recipe="계란찜", have=["달걀"], missing=["소금"], pantry=["소금"])
    assert template_reason(facts) == "계란찜을 바로 만들어 보세요."


def test_template_falls_back_to_counts_when_too_long() -> None:
    facts = RecipeFacts(recipe="아주 긴 이름의 레시피 " * 3, have=["아보카도"], missing=["갯기름나물"])
    assert template_reason(facts).startswith("갯기름나물만 더하면")


# --- checks -------------------------------------------------------------------


def test_good_reasons_pass_all_checks() -> None:
    for row in FRIDGE_ROWS:
        facts = facts_from_row(row)
        assert failed_names(check_reason(facts, GOOD_REASONS[facts.recipe])) == []


def test_check_catches_sibling_ingredient_mixing() -> None:
    facts = facts_from_row(FRIDGE_ROWS[0])
    text = (
        "담백한 두부와 배추김치가 어우러져 깊은 맛을 냅니다. 모차렐라치즈를 더하면 고소한 국물 맛을 살릴 수 있습니다."
    )
    assert "다른카드_재료혼입" in failed_names(check_reason(facts, text, foreign_ingredients={"모차렐라치즈"}))


def test_check_catches_flipped_state_and_pantry_purchase() -> None:
    facts = facts_from_row(FRIDGE_ROWS[0])
    flipped = "고추가 있어서 칼칼한 찌개를 끓이기 좋습니다. 두부와 김치를 넣고 푹 끓이면 든든한 한 끼가 됩니다."
    assert "보유_뒤집힘" in failed_names(check_reason(facts, flipped))
    pushed = "담백한 두부와 배추김치가 어우러져 깊은 맛을 냅니다. 된장을 사서 넣으면 구수한 국물이 완성되는 요리입니다."
    assert "상비재료_구매유도" in failed_names(check_reason(facts, pushed))


def test_check_catches_invented_time_health_and_numbers() -> None:
    facts = facts_from_row(FRIDGE_ROWS[0])
    text = (
        "두부와 돼지고기가 있어 20분이면 건강한 찌개가 완성됩니다. 재료 6가지 중 5가지가 준비되어 고추만 더하면 됩니다."
    )
    names = failed_names(check_reason(facts, text))
    assert {"조리시간_지어냄", "근거_없는_영양건강주장", "숫자_반복"} <= set(names)


def test_check_enforces_length_and_sentence_contract() -> None:
    facts = facts_from_row(FRIDGE_ROWS[0])
    assert "길이미달" in failed_names(check_reason(facts, "두부가 있어요. 고추만 더하세요."))
    assert "출력_형식오류" in failed_names(check_reason(facts, "두부가 있어 좋습니다\n고추만 더하면 됩니다."))
    long_text = (
        GOOD_REASONS["된장 두부찌개"]
        + " 밥 한 공기가 비워지는 든든한 국물이 완성되어 온 가족이 함께 즐기기 좋은 저녁 메뉴입니다."
    )
    assert len(long_text) > 120
    assert "길이초과" in failed_names(check_reason(facts, long_text))


def test_check_does_not_flag_homonyms_as_ingredients() -> None:
    """실험에서 확인된 오탐: `마저`의 `마`, `배어든`의 `배`, `네 가지`의 `가지`."""
    facts = RecipeFacts(recipe="샐러드", have=["아보카도", "양파"], missing=["오렌지"])
    text = (
        "부드러운 아보카도와 알싸한 양파가 샐러드의 기본 식감을 살려 줍니다. "
        "양념이 배어든 오렌지만 마저 곁들이면 네 가지 맛이 어우러집니다."
    )
    assert failed_names(check_reason(facts, text, foreign_ingredients={"마", "배", "가지"})) == []


def test_check_catches_invented_ingredient_via_vocabulary() -> None:
    """세 카드 어디에도 없는 재료는 사전이 있어야 잡힙니다. 한 글자 재료는 사전에서 뺍니다."""
    facts = facts_from_row(FRIDGE_ROWS[0])
    text = "담백한 두부와 배추김치가 어우러져 깊은 맛을 냅니다. 고추와 새우를 더하면 시원한 국물 맛이 살아납니다."
    assert "환각_재료" in failed_names(check_reason(facts, text, vocabulary={"새우", "두부", "파"}))
    assert "환각_재료" not in failed_names(check_reason(facts, text))
    # 한 글자 `파` 는 `대파` 안에 있어도, 사전 단어로는 검사하지 않습니다
    assert "환각_재료" not in failed_names(check_reason(facts, GOOD_REASONS["된장 두부찌개"], vocabulary={"파", "무"}))


def test_sentence_roles_first_have_then_missing() -> None:
    facts = facts_from_row(FRIDGE_ROWS[2])  # 단호박, 마늘, 고추 부족
    ok = (
        "감칠맛 도는 돼지고기와 달큰한 양파, 대파가 볶음의 바탕을 잡아 줍니다. "
        "단호박과 마늘, 고추만 더 담으면 완성돼요."
    )
    assert failed_names(check_reason(facts, ok)) == []
    partial = "감칠맛 도는 돼지고기와 달큰한 양파, 대파가 볶음의 바탕을 잡아 줍니다. 단호박만 더 담으면 완성돼요."
    assert "문장_역할" in failed_names(check_reason(facts, partial))
    swapped = "단호박과 마늘, 고추만 담으면 매콤한 볶음이 완성됩니다. 돼지고기와 양파, 대파가 이미 준비되어 있어요."
    assert "문장_역할" in failed_names(check_reason(facts, swapped))
    no_have = "매콤한 양념이 잘 배어드는 볶음 요리라 밥반찬으로 좋습니다. 단호박과 마늘, 고추만 더 담으면 완성돼요."
    assert "문장_역할" in failed_names(check_reason(facts, no_have))


def test_sentence_roles_for_none_and_many_missing() -> None:
    none = RecipeFacts(recipe="계란찜", have=["달걀"], missing=["소금"], pantry=["소금"])
    assert (
        failed_names(
            check_reason(none, "부드러운 달걀이 폭신한 찜으로 잘 어울립니다. 부족한 재료가 없으니 바로 만들어 보세요.")
        )
        == []
    )
    many = RecipeFacts(recipe="잡채", have=["당면"], missing=["시금치", "당근", "양파", "소고기"])
    good = "쫄깃한 당면이 양념을 잘 머금어 든든한 한 접시가 됩니다. 부족한 재료 몇 가지만 채우면 완성할 수 있어요."
    assert failed_names(check_reason(many, good)) == []
    vague = "쫄깃한 당면이 양념을 잘 머금어 든든한 한 접시가 됩니다. 오늘 저녁 메뉴로 넉넉하게 준비해 보세요."
    assert "문장_역할" in failed_names(check_reason(many, vague))


def test_sentence_roles_handle_aliases_and_similar_names() -> None:
    """`파프리카(착색단고추)` 는 `파프리카` 로 불러도 되고, `방울토마토` 의 뒷글자로 `토마토` 를 잡으면 안 됩니다."""
    facts = RecipeFacts(recipe="곤약백김치말이", have=["곤약(구약나물)"], missing=["부추", "파프리카(착색단고추)"])
    text = (
        "곤약의 탱글탱글한 식감이 백김치와 어우러져 깔끔하게 즐기기 좋습니다. 부추와 파프리카를 더 담으면 완성됩니다."
    )
    assert failed_names(check_reason(facts, text)) == []
    soup = RecipeFacts(recipe="토마토맑은장국", have=["시금치", "토마토"], missing=["방울토마토", "팽이버섯"])
    text = "상큼한 토마토가 국물에 녹아들어 깔끔한 감칠맛을 냅니다. 방울토마토와 팽이버섯만 더 담으면 완성돼요."
    assert failed_names(check_reason(soup, text)) == []


def test_four_or_more_missing_may_be_listed_or_summarized() -> None:
    facts = RecipeFacts(recipe="가지겉절이", have=["가지", "대파"], missing=["고추", "마늘", "매실", "파"])
    listed = (
        "아삭함을 살린 가지가 매콤달콤한 양념과 어우러져 입맛을 돋웁니다. 고추, 마늘, 매실, 파를 더 담으면 완성됩니다."
    )
    assert failed_names(check_reason(facts, listed)) == []


def test_title_ingredient_in_cooking_context_is_not_hallucination() -> None:
    """제목의 `돼지고기` 를 요리 맥락으로 말하는 건 허용하고, 있다·없다·사라 고 하면 잡습니다."""
    facts = RecipeFacts(recipe="참나물돼지고기샐러드", have=["양파"], missing=["참나물", "마늘"])
    ok = "알싸한 양파가 돼지고기의 기름진 맛을 깔끔하게 잡아 줍니다. 참나물과 마늘만 더 담으면 완성돼요."
    assert "환각_재료" not in failed_names(check_reason(facts, ok, vocabulary={"돼지고기"}))
    claim = "돼지고기가 있어 든든한 샐러드를 만들기 좋습니다. 참나물과 마늘만 더 담으면 완성돼요."
    assert "환각_재료" in failed_names(check_reason(facts, claim, vocabulary={"돼지고기"}))


# --- service ------------------------------------------------------------------


async def test_generates_three_cards_with_one_call_each() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recipe = _recipe_of(request)
        calls.append(recipe)
        assert json.loads(request.content)["reasoning"] == {"effort": "minimal"}
        assert request.headers["Authorization"] == "Bearer test-key"
        return _chat_response(GOOD_REASONS[recipe])

    results = await generate_reasons_for_rows(FRIDGE_ROWS, _client(handler))
    assert [result.source for result in results] == ["llm"] * 3
    assert [result.text for result in results] == [GOOD_REASONS[row["name"]] for row in FRIDGE_ROWS]
    assert sorted(calls) == sorted(row["name"] for row in FRIDGE_ROWS)


async def test_cards_are_generated_concurrently() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return _chat_response(GOOD_REASONS[_recipe_of(request)])

    started = time.perf_counter()
    results = await generate_reasons_for_rows(FRIDGE_ROWS, _client(handler))
    elapsed = time.perf_counter() - started
    assert all(result.is_llm for result in results)
    assert elapsed < 0.6, f"순차 실행이면 0.9초 이상입니다: {elapsed:.2f}s"


async def test_timeout_replaces_only_that_card() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        recipe = _recipe_of(request)
        if recipe == "단호박제육볶음":
            await asyncio.sleep(0.5)
        return _chat_response(GOOD_REASONS[recipe])

    results = await generate_reasons_for_rows(FRIDGE_ROWS, _client(handler), timeout_seconds=0.2)
    assert [result.source for result in results] == ["llm", "llm", "fallback_timeout"]
    assert results[2].text == template_reason(facts_from_row(FRIDGE_ROWS[2]))


async def test_check_failure_replaces_only_that_card() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        recipe = _recipe_of(request)
        if recipe == "된장 두부찌개":
            # 옆 카드(가지구이)의 모차렐라치즈가 넘어온 문구
            return _chat_response(
                "담백한 두부와 배추김치가 어우러져 깊은 맛을 냅니다. "
                "모차렐라치즈를 더하면 고소한 국물 맛을 살릴 수 있습니다."
            )
        return _chat_response(GOOD_REASONS[recipe])

    results = await generate_reasons_for_rows(FRIDGE_ROWS, _client(handler))
    assert results[0].source.startswith("fallback_check:")
    assert "다른카드_재료혼입" in results[0].source
    assert results[0].text == "두부를 활용하고 고추만 더하면 된장 두부찌개를 만들 수 있어요."
    assert results[1].is_llm and results[2].is_llm


async def test_http_errors_fall_back_to_template() -> None:
    statuses = iter([500, 402, 429])

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response("", status=next(statuses))

    results = await generate_reasons_for_rows(FRIDGE_ROWS, _client(handler))
    assert [result.source for result in results] == ["fallback_error"] * 3
    assert all(result.text for result in results)


async def test_empty_or_malformed_response_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    results = await generate_reasons_for_rows(FRIDGE_ROWS[:1], _client(handler))
    assert results[0].source == "fallback_error"


async def test_no_client_means_template_only() -> None:
    results = await generate_reasons_for_rows(FRIDGE_ROWS, None)
    assert [result.source for result in results] == ["template"] * 3


async def test_invalid_row_uses_template_without_blocking_others() -> None:
    broken = dict(FRIDGE_ROWS[1], missing_count=5)

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response(GOOD_REASONS[_recipe_of(request)])

    results = await generate_reasons_for_rows([FRIDGE_ROWS[0], broken, FRIDGE_ROWS[2]], _client(handler))
    assert [result.source for result in results] == ["llm", "fallback_invalid_row", "llm"]
    # count 가 어긋나도 재료 목록은 읽히므로 목록 기준 규칙 문구를 씁니다. "바로 만들어" 라고 거짓말하지 않습니다.
    assert results[1].text == "새송이버섯·모차렐라치즈 등 2가지를 더 준비하면 치즈토마토 가지구이를 만들 수 있어요."


async def test_unreadable_row_makes_no_ingredient_claim() -> None:
    row = dict(FRIDGE_ROWS[0], missing_ingredients="{not json")
    results = await generate_reasons_for_rows([row], None)
    assert results[0].source == "fallback_invalid_row"
    assert results[0].text == "된장 두부찌개를 추천해 드려요."


# --- settings -----------------------------------------------------------------


def test_settings_from_env_defaults_and_overrides() -> None:
    with pytest.raises(RuntimeError):
        ReasonSettings.from_env({})
    settings = ReasonSettings.from_env({"OPENROUTER_API_KEY": "k", "REASON_TIMEOUT_SECONDS": "1.5"})
    assert settings.model == "google/gemini-3.5-flash-lite"
    assert settings.timeout_seconds == 1.5
    assert "k" not in repr(settings.model)


def test_request_uses_minimal_reasoning_and_fixed_endpoint() -> None:
    """OpenRouter 의 이 모델은 추론 `none` 을 거부합니다(400). 설정 손잡이 없이 상수로 고정합니다."""
    from rag_lab.reason_service import client as client_module

    assert client_module.REASONING_EFFORT == "minimal"
    assert client_module.BASE_URL == "https://openrouter.ai/api/v1"
