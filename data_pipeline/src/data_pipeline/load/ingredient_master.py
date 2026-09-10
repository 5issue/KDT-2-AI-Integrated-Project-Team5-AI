"""재료 마스터를 공공 영양성분 데이터로 보강합니다.

기존 마스터 736행은 「전국통합식품영양성분정보」의 **원재료성식품(R)** 을 `대표식품` 단위로
접은 것입니다(실측으로 확인: 대표식품 716종 중 715종이 마스터에 있고 키가 95% 일치).
그래서 `버터`·`치즈`·`마요네즈` 같은 가공식품이 통째로 없었고, 레시피 재료의 상당수가
매칭에 실패했습니다.

여기서 하는 일은 둘입니다.
- **가공식품(P) 대표식품을 마스터에 추가**합니다. `is_raw_material=false` 로 넣어
  용어 기준표의 재료/푸드 구분을 지킵니다.
- **`aliases` 를 채웁니다.** 같은 대표식품 아래의 중/소/세분류명을 별칭으로 답니다.
  `난백`·`난황` 이 `달걀` 로 붙는 식입니다.

**이 모듈은 마스터에 쓰는 유일한 경로입니다.** 파이프라인의 다른 부분은 여전히
"매칭만, 추가하지 않음" 정책을 지킵니다. 재료 분류는 팀의 담당 영역이라, 여기서도
공공데이터가 정한 분류를 그대로 옮길 뿐 새 분류를 만들지 않습니다.

측정해 둔 한계도 분명히 적습니다. 이 데이터로 회수되는 것은 미매칭 3,534줄 중
**772줄(22%)** 이고 대부분이 `버터`(429회)입니다. `크림치즈`·`사워크림`·`발사믹식초`·
`식용유`·`베이킹파우더` 는 이 데이터 어디에도 없어 여전히 미매칭입니다.
`계란`->`달걀` 도 이 데이터로는 못 잡습니다(공공데이터가 `달걀` 만 씁니다).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from data_pipeline.batch.raw_source import RawDataset, iter_records
from data_pipeline.domain import ingredient_match_key

# 원재료성식품 / 가공식품 / 음식 구분. `데이터구분코드` 값입니다.
RAW_MATERIAL_CODE = "R"
PROCESSED_CODE = "P"

# 분류가 비어 있을 때 공공데이터가 넣는 값.
EMPTY_MARKERS = frozenset({"", "해당없음", "None", "null"})

# 별칭 후보로 쓸 하위 분류 컬럼. 대표식품보다 좁은 표기라 위로 붙습니다.
ALIAS_COLUMNS = ("식품중분류명", "식품소분류명", "식품세분류명")

REQUIRED_COLUMNS = ("데이터구분코드", "식품대분류코드", "대표식품코드", "대표식품명")

# 집에 늘 있다고 보고 "부족 재료" 계산에서 빼는 재료.
#
# 이건 **큐레이션 목록입니다.** 공공데이터로는 유도할 수 없습니다. 식품대분류 코드가
# 원재료(R)와 가공식품(P)에서 서로 다른 의미라 분류로 가릴 수도 없습니다.
# (K-FIND:07 은 버섯류인데 K-FIND-P:13 은 소금이 있는 군입니다)
#
# 근거: 레시피 재료 등장 빈도 상위이면서 장바구니에 담을 대상이 아닌 것들입니다.
# 이 목록에 없어도 적재에는 지장이 없고, "부족 재료" 화면에 더 나올 뿐입니다.
PANTRY_NAMES = (
    "소금",
    "설탕",
    "간장",
    "고추장",
    "된장",
    "식초",
    "후추",
    "참기름",
    "들기름",
    "물",
    "식용유",
    "올리브유",
    "마요네즈",
    "케첩",
    "전분",
    "밀가루",
    "베이킹파우더",
    "베이킹소다",
    "물엿",
    "꿀",
    "미림",
    "맛술",
    "고춧가루",
    "깨",
    "참깨",
)

# 마스터에 없지만 레시피에 자주 나오는 기본 재료. 없으면 그 재료줄이 통째로 빠집니다.
#
# 이것도 **큐레이션입니다.** 공공 영양성분 데이터 어디에도 없어서 직접 넣습니다.
# 자연키 접두사를 K-FIND 와 갈라 두어 나중에 골라내거나 지울 수 있게 했습니다.
# (name, is_raw_material, 별칭들)
CURATED_BASICS: tuple[tuple[str, bool, tuple[str, ...]], ...] = (
    ("물", True, ("정제수", "생수", "정수")),
    ("식용유", False, ("식물성기름", "식물성유지", "카놀라유", "포도씨유")),
    ("베이킹파우더", False, ()),
    ("베이킹소다", False, ("탄산수소나트륨",)),
    ("바닐라추출물", False, ("바닐라", "바닐라익스트랙", "바닐라에센스")),
    ("크림치즈", False, ()),
    ("사워크림", False, ()),
    ("생크림", False, ("헤비크림", "휘핑크림")),
    ("발사믹식초", False, ()),
    ("우스터소스", False, ()),
)


@dataclass(slots=True)
class MasterRow:
    """마스터에 넣을(혹은 갱신할) 재료 한 행."""

    source_identity_key: str
    name: str
    normalized_name: str
    is_raw_material: bool
    aliases: list[str] = field(default_factory=list)
    is_pantry: bool = False


def _clean(value: Any) -> str:
    """공백을 다듬고 '해당없음' 류를 빈 값으로."""
    text = str(value or "").strip()
    return "" if text in EMPTY_MARKERS else text


def identity_key(kind: str, group: str, code: str, name: str) -> str:
    """기존 마스터와 같은 모양의 자연키.

    원재료성식품은 기존 736행과 충돌하지 않도록 **같은 형식**을 그대로 씁니다.
        K-FIND:01:01018:국수
    가공식품은 대표식품코드가 원재료와 10건 겹치므로 구분자를 넣습니다.
        K-FIND-P:19:19801:버터
    """
    prefix = "K-FIND" if kind == RAW_MATERIAL_CODE else f"K-FIND-{kind}"
    return f"{prefix}:{group}:{code}:{name}"


def build_master_rows(datasets: list[RawDataset]) -> list[MasterRow]:
    """영양성분 데이터셋들에서 마스터 행을 만듭니다.

    `데이터구분코드` 가 R / P 인 레코드만 씁니다. 음식(D)은 재료가 아니라 완성된 요리라
    재료 마스터에 넣지 않습니다.
    """
    aliases: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)

    for dataset in datasets:
        if not all(column in dataset.columns for column in REQUIRED_COLUMNS):
            continue
        for record in iter_records(dataset):
            payload = record.payload
            kind = _clean(payload.get("데이터구분코드"))
            if kind not in (RAW_MATERIAL_CODE, PROCESSED_CODE):
                continue
            group = _clean(payload.get("식품대분류코드"))
            code = _clean(payload.get("대표식품코드"))
            name = _clean(payload.get("대표식품명"))
            if not (group and code and name):
                continue

            bucket = aliases[(kind, group, code, name)]
            for column in ALIAS_COLUMNS:
                alias = _clean(payload.get(column))
                # 대표식품명과 같은 별칭은 의미가 없습니다.
                if alias and ingredient_match_key(alias) != ingredient_match_key(name):
                    bucket.add(alias)

    rows = [
        MasterRow(
            source_identity_key=identity_key(kind, group, code, name),
            name=name,
            normalized_name=name,
            is_raw_material=(kind == RAW_MATERIAL_CODE),
            aliases=sorted(bucket),
        )
        for (kind, group, code, name), bucket in aliases.items()
    ]
    rows.extend(
        MasterRow(
            source_identity_key=f"TEAM-BASIC:{name}",
            name=name,
            normalized_name=name,
            is_raw_material=is_raw,
            aliases=sorted(alias_names),
        )
        for name, is_raw, alias_names in CURATED_BASICS
    )

    pantry = {ingredient_match_key(name) for name in PANTRY_NAMES}
    for row in rows:
        row.is_pantry = ingredient_match_key(row.name) in pantry

    rows.sort(key=lambda item: item.source_identity_key)
    return rows


def to_staging_tuples(rows: list[MasterRow]) -> list[tuple[Any, ...]]:
    """COPY 로 밀어넣을 튜플."""
    return [
        (row.source_identity_key, row.name, row.normalized_name, row.is_raw_material, row.aliases, row.is_pantry)
        for row in rows
    ]


def render(rows: list[MasterRow]) -> str:
    """사람이 읽을 요약."""
    raw = sum(1 for row in rows if row.is_raw_material)
    with_alias = sum(1 for row in rows if row.aliases)
    alias_total = sum(len(row.aliases) for row in rows)
    pantry = sum(1 for row in rows if row.is_pantry)
    return (
        f"마스터 후보 {len(rows)}종 (원재료 {raw} / 가공식품 {len(rows) - raw})\n"
        f"  별칭이 있는 재료 {with_alias}종, 별칭 총 {alias_total}개\n"
        f"  상비재료(is_pantry) {pantry}종"
    )
