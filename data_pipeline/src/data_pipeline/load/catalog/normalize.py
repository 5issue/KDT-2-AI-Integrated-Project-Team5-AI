"""raw 값을 DB 타입에 맞추는 변환. **순수 함수입니다.**

raw 는 출처가 제각각이라 숫자가 문자열로 오고, 빈 값이 `"None"`·`"nan"`·`"-"` 로 오고,
중량이 상품명 안에 섞여 옵니다. 여기서 하는 일은 그것을 한 모양으로 모으는 것뿐입니다.

판단은 없습니다. 모르는 값은 버리고 `None` 을 냅니다 - 특히 보관 유형은
**임의로 셋 중 하나에 끼워 넣지 않습니다.** 틀린 보관법은 없는 것보다 나쁩니다.
버려진 값은 `CatalogRows` 에 쌓여 적재 리포트에 드러납니다.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from data_pipeline.load.catalog.models import CatalogRows

# raw 가 문자열로 실어 보내는 빈 값들.
NULLISH = frozenset({"", "none", "null", "nan", "-"})

# 원본(Kurly) 보관 유형 -> 서비스 표기.
#
# 화면에는 어차피 한국어로 나갑니다. 영어 상수로 적재해 두면 서빙에서 한 번, 프런트에서
# 한 번 되돌려야 하고, 그 대응표가 두 벌이 되면 언젠가 갈라집니다. 들어올 때 한 번만
# 바꿔 두는 편이 낫습니다. `docs/product-ingredient-storage-normalization-guide.md`
# 4.3 절의 대응과 같습니다.
#
# 모르는 값은 지어내지 않고 **비웁니다.** 현재 raw 2,553행에는 아래 3종과
# 빈 값(1,551행)뿐이지만, 원본이 늘면서 새 표기가 들어올 수 있습니다.
#
# 원문 그대로 통과시키던 때가 있었는데, 그 뒤 alembic 0007 이 `ck_product_storage_type`
# 으로 셋 중 하나만 받도록 했습니다. 그대로 두면 새 표기 한 건에 적재 전체가 롤백됩니다.
# 임의로 셋 중 하나에 끼워 넣는 것도 안 됩니다. 틀린 보관법은 없는 것보다 나쁩니다.
# 버려진 값은 적재 리포트에 남아 눈에 띕니다.
PRODUCT_STORAGE_TYPES: dict[str, str] = {
    "COLD": "냉장",
    "FROZEN": "냉동",
    "AMBIENT_TEMPERATURE": "상온",
}


def _text(value: Any) -> str | None:
    """빈 값 표기를 전부 None 으로 모읍니다. parquet 이 'None' 문자열로 싣고 옵니다."""
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in NULLISH else text


def _number(value: Any, places: int) -> Decimal | None:
    """NUMERIC 컬럼용. 콤마나 통화 기호가 섞여 와도 숫자만 추립니다."""
    text = _text(value)
    if text is None:
        return None
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    try:
        return Decimal(str(round(float(cleaned), places)))
    except (ValueError, InvalidOperation):
        return None


def _integer(value: Any) -> int | None:
    """INTEGER 컬럼용."""
    number = _number(value, 0)
    return int(number) if number is not None else None


def _storage_type(value: Any, rows: CatalogRows | None = None) -> str | None:
    """상품 보관 유형을 서비스 표기(한국어)로 맞춥니다. 모르는 값은 비웁니다."""
    text = _text(value)
    if text is None:
        return None
    if text in PRODUCT_STORAGE_TYPES.values():
        return text
    mapped = PRODUCT_STORAGE_TYPES.get(text.upper())
    if mapped is None and rows is not None:
        rows.unknown_storage_types[text] = rows.unknown_storage_types.get(text, 0) + 1
    return mapped


def _finite(value: Any) -> Any:
    """NaN / Infinity 를 None 으로 바꿉니다.

    파이썬 json 은 NaN 을 그대로 뱉지만 PostgreSQL JSON 은 거부합니다
    (`Token "NaN" is invalid`). 크롤 데이터의 빈 수치가 NaN 으로 들어와 실제로 밟았습니다.
    """
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value


def _jsonb(value: Any) -> str:
    """metadata 는 JSONB 라 문자열로 넘깁니다. 파싱이 안 되면 원문을 담아 둡니다."""
    text = _text(value)
    if text is None:
        return "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return json.dumps({"raw": text}, ensure_ascii=False)
    payload = _finite(parsed if isinstance(parsed, dict) else {"raw": parsed})
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


# 브랜드 이름 길이 상한. alembic 0011 의 `product.brand_name VARCHAR(100)` 과 같습니다.
BRAND_NAME_LENGTH = 100


def _brand_name(payload: dict[str, Any]) -> str | None:
    """브랜드 이름. 최상위 `brand` 를 먼저 보고, 없으면 `metadata.brand` 를 봅니다.

    지금 raw(`product_raw.parquet`)는 `metadata` 안에만 싣고 오는데, 원천이 정리되면서
    최상위 컬럼으로 올라올 수 있습니다. 둘 다 보면 raw 가 어느 쪽이든 동작합니다.

    **`brand_id` 가 아니라 이름을 담습니다.** `brand` 테이블이 없고, 지금 브랜드에
    달 속성이 이름뿐이라 표를 만들 이유가 없습니다(alembic `0011` 의 사유 참고).
    """
    direct = _text(payload.get("brand"))
    if direct:
        return direct[:BRAND_NAME_LENGTH]

    metadata = payload.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return None
    if not isinstance(metadata, dict):
        return None
    nested = _text(metadata.get("brand"))
    return nested[:BRAND_NAME_LENGTH] if nested else None


# 상품명에서 중량을 읽을 때 쓰는 표기. 앞에 오는 수치와 짝지어 봅니다.
_MASS_UNITS = {"kg": 1000, "킬로": 1000, "g": 1, "그램": 1}
_VOLUME_UNITS = ("l", "리터", "ml", "밀리")
_WEIGHT_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|킬로|g|그램|ml|밀리|l|리터)\b", re.IGNORECASE)


def _weight_grams(value: Any, product_name: str) -> Decimal | None:
    """중량을 그램으로 맞춥니다.

    raw 변환이 단위를 빼고 숫자만 실어 보내서 `유기농황설탕 1kg` 이 `weight_g = 1` 로
    들어왔습니다(235건). 상품명에 단위가 남아 있으므로 거기서 읽어 보정합니다.

    부피 단위(L/ml)만 있는 상품은 무게를 알 수 없습니다. 밀도를 1 로 가정해 넣으면
    기름이나 시럽에서 크게 틀리므로 **null 로 둡니다.** 틀린 값보다 없는 값이 낫습니다.
    """
    raw = _number(value, 2)
    match = _WEIGHT_PATTERN.search(product_name or "")
    if match is None:
        return raw

    amount = Decimal(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if unit in _MASS_UNITS:
        return Decimal(str(round(float(amount * _MASS_UNITS[unit]), 2)))
    if unit in _VOLUME_UNITS:
        return None
    return raw
