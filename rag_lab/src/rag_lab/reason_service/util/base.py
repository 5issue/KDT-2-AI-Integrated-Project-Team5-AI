"""검사 결과(``Check``)와 규칙이 받는 입력(``CheckTarget``)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..facts import RecipeFacts
from .text import without_title


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CheckTarget:
    """규칙 하나가 보는 것 전부. ``check_reason`` 이 한 번 만들어 모든 규칙에 넘깁니다."""

    facts: RecipeFacts
    reason: str
    # 레시피 이름 인용을 지운 문구. 제목 안의 재료를 언급으로 치지 않으려고 대부분의 규칙이 이것을 봅니다.
    scannable: str
    # 같은 화면 다른 카드의 재료
    foreign_ingredients: frozenset[str]
    # 재료 이름 전체 사전(``ingredient`` 표)
    vocabulary: frozenset[str]

    @classmethod
    def of(
        cls,
        facts: RecipeFacts,
        reason: str,
        *,
        foreign_ingredients: Iterable[str] = (),
        vocabulary: Iterable[str] = (),
    ) -> CheckTarget:
        foreign, lexicon = frozenset(foreign_ingredients), frozenset(vocabulary)
        return cls(
            facts=facts,
            reason=reason,
            scannable=without_title(reason, facts, also_keep=foreign | lexicon),
            foreign_ingredients=foreign,
            vocabulary=lexicon,
        )
