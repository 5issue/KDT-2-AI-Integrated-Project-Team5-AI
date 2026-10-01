"""여러 규칙이 함께 쓰는 한국어 재료명 도우미.

동음이의어 오탐 목록(`가지`, `배`, `마`)은 실험에서 실제로 부딪힌 것들이며, 지우기 전에 테스트를 먼저 보세요.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..facts import RecipeFacts

# 목적격 조사 `을`/`를`도 넣습니다. `된장을 사서` 는 상비재료 구매 유도입니다.
PARTICLES = r"(?:은|는|이|가|도|만|과|와|랑|이랑|을|를)?"
HAS_WORDS = r"(?:있(?!으면|다면|으시면)|남았|갖(?!춘|추)|보유|충분)"
LACKS_WORDS = r"(?:없|부족|빠졌|모자라|떨어)"
BUY_WORDS = r"(?:사|사서|사면|구매|주문|장바구니|담으|준비하)"

# 재료명이면서 흔한 다른 말이기도 한 것. 해당 문맥에서는 재료로 보지 않습니다.
# `가지`: 수량 단위(`네 가지`)와 동사(`가지고`). `배`: `배가시키다`, `양념이 배는`, `배어든`.
# `마`: `마저`, `마지막`, `마무리`. 실험 중 확인된 오탐만 넣습니다.
_AMBIGUOUS_WORDS: dict[str, str] = {
    "가지": r"(?:(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇|여러|\d+)\s*가지|가지(?=[고면며]))",
    "배": r"(?:배가(?=[시하되])|(?<=[가-힣]이 )배는|배어|배는|배게)",
    "마": r"(?:마저|마지막|마무리|마련|마음|마냥)",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+")


def word_pattern(name: str) -> str:
    # 앞에 한글이 붙어 있으면 다른 단어의 일부입니다(`양파` 안의 `파`).
    return r"(?<![가-힣])" + re.escape(name)


def mentions(reason: str, name: str, verb_group: str) -> bool:
    """`name` 뒤에 조사와 `verb_group`(있다·없다·사다)이 이어지는가."""
    return re.search(word_pattern(name) + PARTICLES + r"\s*" + verb_group, reason) is not None


def appears_as_ingredient(word: str, reason: str) -> bool:
    if re.search(word_pattern(word), reason) is None:
        return False
    if pattern := _AMBIGUOUS_WORDS.get(word):
        return re.search(word_pattern(word), re.sub(pattern, " ", reason)) is not None
    return True


def is_part_of_known(word: str, known: set[str]) -> bool:
    return any(word != name and word in name for name in known)


def split_sentences(reason: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(reason.strip()) if part]


def aliases(name: str) -> list[str]:
    """`파프리카(착색단고추)` 는 `파프리카` 로도, `착색단고추` 로도 불립니다."""
    names = [name]
    if "(" in name and name.endswith(")"):
        base, inner = name[:-1].split("(", 1)
        names += [part for part in (base.strip(), inner.strip()) if part]
    return names


def mentioned(name: str, text: str, *, others: Iterable[str] = ()) -> bool:
    """재료가 언급됐는가.

    `새송이버섯` 을 `버섯` 으로 줄여 부른 것도 인정합니다. 단, 그 줄임말이 이 상황의 **다른** 재료
    안에도 들어 있으면 인정하지 않습니다. `방울토마토` 의 `마토` 로 `토마토` 언급을 잡으면 안 됩니다.
    """
    for alias in aliases(name):
        if re.search(word_pattern(alias), text) is not None:
            return True
        # ponytail: 뒤 두 글자 휴리스틱. `돼지고기` 를 `고기` 로 부른 것도 통과함. 오탐이 보이면 재료별 별칭 표로.
        suffix = alias[-2:]
        if len(alias) > 2 and suffix in text and not any(suffix in other for other in others if other != name):
            return True
    return False


def without_title(reason: str, facts: RecipeFacts, *, also_keep: Iterable[str] = ()) -> str:
    """레시피 이름 인용을 지웁니다. 그 이름을 품은 더 긴 재료명은 남깁니다.

    레시피 `라자냐` 를 그냥 지우면 부족 재료 `라자냐면` 이 ` 면` 으로 잘려, `문장_역할` 은 "부족 재료 안내가
    없음" 으로 오탐하고 지어낸 재료 검사는 놓칩니다.

    ``also_keep`` 은 이 상황 밖의 재료명(옆 카드 재료, 재료 사전)입니다. `라자냐면` 이 사전에만 있어도 같은
    이유로 남깁니다.
    """
    if not facts.recipe:
        return reason
    # 긴 이름을 먼저 두어야 정규식이 `라자냐면` 을 통째로 잡고 남깁니다.
    longer = sorted(
        {name for name in (*facts.known_ingredients, *also_keep) if facts.recipe in name and name != facts.recipe},
        key=lambda name: (-len(name), name),
    )
    pattern = "|".join(re.escape(name) for name in [*longer, facts.recipe])
    return re.sub(pattern, lambda match: " " if match.group() == facts.recipe else match.group(), reason)


def title_context_only(word: str, facts: RecipeFacts, text: str) -> bool:
    """레시피 제목에 든 재료를 요리 맥락으로만 말한 경우. 보유·부재·구매 주장을 하면 예외가 아닙니다."""
    if word not in facts.recipe:
        return False
    pattern = r"(?<![가-힣])" + re.escape(word) + PARTICLES + r"[^.!?。？！\n]{0,10}"
    return not any(re.search(pattern + group, text) for group in (HAS_WORDS, LACKS_WORDS, BUY_WORDS))
