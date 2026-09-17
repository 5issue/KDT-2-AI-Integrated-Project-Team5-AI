"""COOKRCP01 의 재료 문자열 파서. **순수 함수만 있습니다.**

DB 도 파일도 보지 않습니다. 문자열을 넣으면 문자열과 숫자가 나옵니다.
그래서 이 파일의 테스트는 픽스처도 커넥션도 필요 없습니다.

## 왜 여기가 긴가

`RCP_PARTS_DTLS` 는 자유 문자열이지만 형식이 일정합니다.

    ●방울토마토 소박이 :
    방울토마토 150g(5개), 양파 10g(3x1cm), 부추 10g(5줄기)
    ●양념장 :
    고춧가루 4g(1작은술), 멸치액젓 3g(2/3작은술)

`●`·`·`·줄바꿈으로 구획을 나누고, 구획 제목(`... :`)을 버리고, 쉼표로 끊은 뒤
이름과 수량을 가릅니다. **판단이 아니라 규칙이라 파이썬에 둡니다.**

여기 있는 정규식과 예외는 전부 실제 데이터에 부딪혀 나온 것들입니다. 주석에 몇 건이
걸렸는지 적어 두었으니, 지우기 전에 `tests/test_recipe_catalog.py` 를 먼저 보세요.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Any

from data_pipeline.domain import ingredient_match_key

# 원본에 HTML 태그가 섞여 있습니다(`<br>` 42회, `<br />` 6회, `<strong>` 등).
# 떼지 않으면 `<br>` 이 재료명으로 잡혀 미매칭 목록을 더럽힙니다. 실제로 21건 나왔습니다.
_HTML_TAG = re.compile(r"<[^>]{1,20}>")

# 구획을 나누는 것들. `●양념장 :` 같은 소제목이 이 뒤에 옵니다.
# `•` 도 구획 기호로 쓰입니다(`• [가정 간편식 재료] ...`).
_SECTION = re.compile(r"[●·▶∙•]|\n")

# `연두부 75g(3/4모)` 에서 이름만. 수량이 없는 `참깨 약간` 도 받습니다.
_ITEM = re.compile(r"^(?P<name>[^0-9(]+?)\s*(?P<qty>[\d./⅓⅔¼½¾]+\s*[a-zA-Z가-힣]*)?\s*(\(.*\))?$")

# `[1인분]`, `2인분 기준` 같은 머리말.
_PORTION = re.compile(r"^\[[^\]]*\]|^\d+인분\s*(기준)?\s*[:：]?")

# `양념장 : ` 같은 구획 제목.
_SECTION_TITLE = re.compile(r"^[^:：]{1,14}\s*[:：]\s*")

# 수량이 아니라 상태를 적은 꼬리. 재료명에서 뗍니다.
_TRAILING = re.compile(r"\s*(약간|적당량|조금|소량)$")


def _text(value: Any) -> str:
    """parquet 이 'None' 문자열로 싣고 오는 빈 값을 정리합니다."""
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() in {"", "none", "null", "nan", "-"} else text


def parse_ingredients(raw: str) -> list[tuple[str, str]]:
    """`RCP_PARTS_DTLS` 에서 (재료명, 수량표기) 목록을 뽑습니다.

    수량은 원문 표기를 그대로 둡니다. `75g` 를 숫자로 쪼개는 것은 부피->무게 환산과
    얽혀 있어 이번 스프린트 제외 항목(2번)입니다.
    """
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    # HTML 을 줄바꿈으로 바꿉니다. `<br>` 이 구획 구분자 노릇을 하고 있습니다.
    cleaned = _HTML_TAG.sub("\n", raw or "")

    for chunk in _SECTION.split(cleaned):
        chunk = chunk.strip()
        if not chunk or chunk.endswith((":", "：")):
            continue
        chunk = _PORTION.sub("", chunk).strip()
        chunk = _SECTION_TITLE.sub("", chunk).strip()

        for part in chunk.split(","):
            part = part.strip()
            if not part:
                continue
            match = _ITEM.match(part)
            name = (match.group("name") if match else part).strip()
            quantity = (match.group("qty") or "").strip() if match else ""
            name = _TRAILING.sub("", name).strip()

            # **한 글자도 받습니다.** 한국어 재료에는 한 음절이 흔합니다 —
            # 마스터 1,028종 중 41종이 한 글자이고(물·꿀·무·배·쌀·파·잣·밤·김),
            # 버리면 이 데이터에서만 648줄이 사라집니다.
            # 매칭은 어차피 마스터에 있는 이름만 붙으므로 오탐 위험이 낮습니다.
            if not name or name[0].isdigit():
                continue
            key = ingredient_match_key(name)
            if key in seen:
                continue
            seen.add(key)
            items.append((name, quantity))
    return items


# 손질 상태 접두어. `다진 마늘` 과 `마늘` 은 같은 재료입니다.
#
# **상태만 뗍니다.** `고춧가루`/`마늘가루` 처럼 가루는 별도 재료라 건드리지 않습니다
# (정규화 가이드 3.4). `후춧가루 -> 후추`, `닭가슴살 -> 닭고기` 같은 것은 판단이 필요해
# 여기서 하지 않고 3단계 `resolve` 로 넘깁니다. 규칙과 판단을 섞지 않습니다.
_PREP_PREFIX = re.compile(
    r"^(굵게\s*다진|잘게\s*다진|곱게\s*다진|다진|다져놓은|"
    r"채\s*썬|채썰은|어슷\s*썬|송송\s*썬|편\s*썬|저민|썬|"
    r"삶은|데친|구운|볶은|찐|절인|말린|건조|불린|깐|손질한|손질|다듬은)\s*"
)
_PREP_SUFFIX = re.compile(r"(다진\s*것|채썬\s*것|간\s*것|썰은\s*것)$")


def match_candidates(name: str) -> list[str]:
    """이 재료명으로 시도해 볼 표기들. 앞에 있는 것부터 씁니다.

    원문을 먼저 보고, 안 맞으면 손질 상태를 뗀 형태를 봅니다.
    `올리브오일`/`올리브유` 처럼 같은 뜻의 표기 차이도 함께 봅니다.
    """
    seen: list[str] = []

    def add(value: str) -> None:
        # 한 글자도 받습니다. 마스터에 물·꿀·무·배 같은 한 음절 재료가 41종 있습니다.
        value = value.strip()
        if value and value not in seen:
            seen.append(value)

    add(name)
    without_suffix = _PREP_SUFFIX.sub("", name).strip()
    add(without_suffix)
    for base in (name, without_suffix):
        add(_PREP_PREFIX.sub("", base))
    for base in list(seen):
        if base.endswith("오일"):
            add(base[:-2] + "유")
    return seen


def _unit(quantity: str) -> str | None:
    """`75g` 에서 단위만. 숫자를 떼고 남는 것이 단위입니다."""
    unit = re.sub(r"[\d./⅓⅔¼½¾\s]", "", quantity or "")
    return unit[:30] or None


# 유니코드 분수 기호. 원천이 `½큰술` 처럼 쓰는 일이 있습니다.
_VULGAR_FRACTIONS = {
    "⅓": Fraction(1, 3),
    "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4),
    "½": Fraction(1, 2),
    "¾": Fraction(3, 4),
}

# **순서가 중요합니다.** 대분수 -> 분수 -> 소수 순으로 시도합니다.
# 소수를 먼저 보면 `1/2` 의 `1` 만 먹고 분수를 놓칩니다(실제로 그렇게 틀렸습니다).
_MIXED_NUMBER = re.compile(r"^(\d+)\s+(\d+)\s*/\s*(\d+)")
_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)")
_PLAIN_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)")


def _quantity(raw: str) -> Decimal | None:
    """수량 표기에서 숫자만. **분수를 분수로 계산합니다.**

    예전에는 `re.sub(r"[^\\d.]", "", raw)` 로 숫자가 아닌 글자를 전부 지웠습니다.
    그러면 `1/2알` 에서 `/` 까지 사라져 `"12"` 가 남고, 수량이 **12** 로 저장됐습니다.
    COOKRCP01 5,813개 수량 표기 중 2건이 실제로 그렇게 들어갔습니다.

    계산이 안 되면 **`None` 을 돌려줍니다.** 원문은 `raw_text` 에 그대로 남으므로
    틀린 숫자를 남기는 것보다 비우는 편이 낫습니다(`모르는 값은 지어내지 않는다`).
    """
    text = (raw or "").strip()
    if not text:
        return None

    total = Fraction(0)
    found = False
    for symbol, value in _VULGAR_FRACTIONS.items():
        if symbol in text:
            total += value
            text = text.replace(symbol, " ")
            found = True

    text = text.lstrip()
    if match := _MIXED_NUMBER.match(text):
        whole, numerator, denominator = (int(value) for value in match.groups())
        if denominator == 0:
            return None
        total += whole + Fraction(numerator, denominator)
        found = True
    elif match := _FRACTION.match(text):
        numerator, denominator = (int(value) for value in match.groups())
        if denominator == 0:
            return None
        total += Fraction(numerator, denominator)
        found = True
    elif match := _PLAIN_NUMBER.match(text):
        total += Fraction(match.group(1))
        found = True

    if not found:
        return None
    return Decimal(str(round(float(total), 2)))
