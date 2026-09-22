"""서빙이 부를 공개 함수. SQL 행 N개를 받아 문구 N개를 돌려줍니다.

- 카드마다 LLM 1회, 한 요청의 카드는 ``asyncio.gather`` 로 동시에 생성합니다.
- 카드마다 제한 시간을 두고, 시간 초과·오류·검사 실패는 **그 카드만** 규칙 기반 문구로 바꿉니다.
- LLM 없이(``client=None``) 부르면 전부 규칙 기반 문구입니다. 스위치를 끈 상태와 같습니다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .checks import check_reason, failed_names
from .facts import RecipeFacts, facts_from_row, validate_facts
from .prompt import SYSTEM_PROMPT, build_user_prompt
from .template import object_particle, template_reason

logger = logging.getLogger(__name__)


class ReasonClient(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ReasonResult:
    """문구와 그 출처. ``source`` 는 ``llm`` / ``template`` / ``fallback_...`` 입니다."""

    text: str
    source: str

    @property
    def is_llm(self) -> bool:
        return self.source == "llm"


async def _generate_one(
    facts: RecipeFacts,
    client: ReasonClient,
    *,
    timeout_seconds: float,
    foreign: set[str],
    vocabulary: frozenset[str],
) -> ReasonResult:
    fallback = template_reason(facts)
    try:
        text = await asyncio.wait_for(client.complete(SYSTEM_PROMPT, build_user_prompt(facts)), timeout_seconds)
    except TimeoutError:
        return ReasonResult(fallback, "fallback_timeout")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # ReasonClientError 는 RuntimeError 입니다. 원인 종류만 남기고 본문·키는 남기지 않습니다.
        logger.info("추천 이유 생성 실패, 템플릿으로 대체: %s", type(exc).__name__)
        return ReasonResult(fallback, "fallback_error")
    failed = failed_names(check_reason(facts, text, foreign_ingredients=foreign, vocabulary=vocabulary))
    if failed:
        return ReasonResult(fallback, "fallback_check:" + ",".join(failed))
    return ReasonResult(text, "llm")


async def generate_reasons(
    facts_list: list[RecipeFacts],
    client: ReasonClient | None,
    *,
    timeout_seconds: float = 2.0,
    vocabulary: Iterable[str] = (),
) -> list[ReasonResult]:
    """카드 목록의 문구를 동시에 만듭니다. 순서는 입력과 같습니다.

    ``vocabulary`` 는 재료 이름 전체 사전입니다. 서빙은 앱 시작 때 ``ingredient`` 표에서 한 번 읽어 넘깁니다.
    비워 두면 옆 카드 재료 혼입만 잡고, 세 카드 어디에도 없는 재료를 지어낸 것은 잡지 못합니다.
    """
    if client is None or not facts_list:
        return [ReasonResult(template_reason(facts), "template") for facts in facts_list]
    all_ingredients = [facts.known_ingredients for facts in facts_list]
    lexicon = frozenset(vocabulary)
    tasks = []
    for index, facts in enumerate(facts_list):
        foreign = set().union(*(names for other, names in enumerate(all_ingredients) if other != index))
        tasks.append(_generate_one(facts, client, timeout_seconds=timeout_seconds, foreign=foreign, vocabulary=lexicon))
    results = await asyncio.gather(*tasks)
    fallbacks = sum(not result.is_llm for result in results)
    logger.info("추천 이유 생성: 카드 %d장, LLM %d장, 대체 %d장", len(results), len(results) - fallbacks, fallbacks)
    return list(results)


async def generate_reasons_for_rows(
    rows: list[dict[str, Any]],
    client: ReasonClient | None,
    *,
    timeout_seconds: float = 2.0,
    vocabulary: Iterable[str] = (),
) -> list[ReasonResult]:
    """``my_recipe_candidates`` 행 목록을 받습니다. 사실값이 어긋난 행은 LLM 에 넘기지 않습니다.

    - 목록은 읽히는데 count 가 안 맞으면: 목록 기준 규칙 문구 (목록이 화면의 재료 태그와 같은 출처)
    - 목록조차 못 읽으면: 재료에 대해 아무 주장도 하지 않는 문구
    """
    fixed: dict[int, ReasonResult] = {}
    valid: list[RecipeFacts] = []
    for index, row in enumerate(rows):
        recipe = str(row.get("name", ""))
        try:
            facts = facts_from_row(row)
        except (ValueError, KeyError) as exc:
            logger.warning("추천 행 %d의 재료 목록을 읽지 못했습니다: %s", index, type(exc).__name__)
            fixed[index] = ReasonResult(f"{recipe}{object_particle(recipe)} 추천해 드려요.", "fallback_invalid_row")
            continue
        try:
            validate_facts(facts)
        except ValueError as exc:
            logger.warning("추천 행 %d의 사실값이 어긋나 규칙 문구만 씁니다: %s", index, exc)
            fixed[index] = ReasonResult(template_reason(facts), "fallback_invalid_row")
            continue
        valid.append(facts)
    generated = iter(await generate_reasons(valid, client, timeout_seconds=timeout_seconds, vocabulary=vocabulary))
    return [fixed[index] if index in fixed else next(generated) for index in range(len(rows))]
