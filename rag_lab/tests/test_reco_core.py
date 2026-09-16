"""추천 문구 **생성** 검증. DB 도 API 도 쓰지 않습니다.

`rag_lab.recommendation.core` — 서빙이 실제로 쓰는 경로입니다.
"""

from __future__ import annotations

import pytest
from reco_helpers import case

from rag_lab.recommendation.core import build_situation, case_fingerprint, recommendation_reason
from rag_lab.testing import FakeChatClient


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
