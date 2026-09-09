"""1단계 프로파일에 결정적 제약을 적용합니다.

LLM 을 두 모델로 두 번 돌려 본 결과가 이 모듈의 근거입니다.
**판단은 잘하고 제약 준수는 반복 실패했습니다.**

- 잘한 것: 이게 무슨 데이터인지, 적재 대상인지, 어느 컬럼이 무슨 뜻인지, 어느 짝과 붙는지
- 반복 실패: "겹치면 하나만 골라라", "A 를 고르면 B 도 반드시", "존재하는 컬럼만 써라"

앞은 판단이고 뒤는 **검증 가능한 불변식**입니다. 그래서 뒤쪽만 여기로 내렸습니다.
`domain.SLOT_DERIVATION` 을 파이썬에 둔 것과 같은 이유입니다. 텍스트 패턴 매칭이 아니라
실제 데이터와 DB 제약으로 참·거짓이 갈리는 것들입니다.

여기서 하는 판단은 전부 raw 데이터를 실제로 읽어 확인합니다. 데이터셋 이름을 조건에
적어 두지 않으므로 새 raw 가 들어와도 그대로 동작합니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from data_pipeline.batch.raw_source import RawDataset, iter_records
from data_pipeline.domain import STORAGE_SLOTS
from data_pipeline.schemas import DatasetProfile, TargetTable

# 값 겹침을 볼 때 읽을 최대 행 수. 자연키 후보를 가리는 데는 이 정도면 충분합니다.
VALUE_SAMPLE_ROWS = 5_000

# 두 데이터셋이 같은 엔티티를 나눠 가졌다고 볼 겹침 비율(작은 쪽 기준).
COMPANION_MIN_OVERLAP = 0.8


@dataclass(slots=True)
class Adjustment:
    """제약 하나가 프로파일을 고친 기록. 사람이 검토할 수 있게 남깁니다."""

    dataset: str
    rule: str
    detail: str

    def render(self) -> str:
        """한 줄 요약."""
        return f"[{self.rule}] {self.dataset}: {self.detail}"


def _column_values(dataset: RawDataset, column: str, *, limit: int = VALUE_SAMPLE_ROWS) -> set[str]:
    """컬럼 하나의 값 집합. 없는 컬럼이면 빈 집합입니다."""
    if column not in dataset.columns:
        return set()
    values: set[str] = set()
    for index, record in enumerate(iter_records(dataset)):
        if index >= limit:
            break
        value = record.payload.get(column)
        if value is not None and str(value).strip():
            values.add(str(value))
    return values


def _slot_column(dataset: RawDataset) -> str | None:
    """storage_guideline 의 9개 슬롯 열거형을 값으로 가진 컬럼 이름. 없으면 None.

    있으면 이미 타깃 테이블 모양(한 행이 한 슬롯)으로 펼쳐진 소스라는 뜻입니다.
    넓은 원본(컬럼 수십 개에 슬롯이 흩어진 형태)보다 이쪽이 변환 위험이 훨씬 낮습니다.
    """
    slots = set(STORAGE_SLOTS)
    for column in dataset.columns:
        values = _column_values(dataset, column)
        if values and values <= slots:
            return column
    return None


def find_duplicate_groups(datasets: Sequence[RawDataset]) -> list[list[str]]:
    """같은 원본의 사본끼리 묶습니다.

    컬럼 **이름 집합**과 행 수가 같으면 사본으로 봅니다. 타입만 다른 사본이 실제로 있어서
    (`foodkeeper_product` 는 수치형, `foodkeeper_xls_product` 는 전부 문자열인 같은 661행)
    타입은 비교에 넣지 않습니다.
    """
    groups: dict[tuple[tuple[str, ...], int], list[str]] = {}
    for dataset in datasets:
        key = (tuple(sorted(dataset.columns)), dataset.row_count)
        groups.setdefault(key, []).append(dataset.name)
    return [sorted(names) for names in groups.values() if len(names) > 1]


def _is_surrogate_id(values: set[str]) -> bool:
    """값이 전부 정수면 대리키(surrogate id)로 봅니다.

    서로 다른 표의 `ID` 는 각자 1 부터 세는 별개 번호라 겹침이 무조건 1.0 이 나옵니다.
    실제로 `foodkeeper_xls_category.ID`(1~25) 가 `foodkeeper_product.ID`(1~661) 에
    통째로 포함되어 둘이 짝으로 잡혔습니다. 이름이 같은 정수 컬럼은 근거가 못 됩니다.
    """
    return all(value.lstrip("-").isdigit() for value in values)


def find_companions(datasets: Sequence[RawDataset]) -> dict[str, list[str]]:
    """한 엔티티를 나눠 가진 데이터셋 짝을 실측으로 찾습니다.

    같은 이름의 컬럼을 공유하고 그 **값 집합이 실제로 겹치면** 짝입니다.
    사본(컬럼 구성이 같음)은 짝이 아니므로 제외합니다.

    이름이나 스키마만 보는 LLM 판단과 달리, 여기서는 값을 읽어 확인합니다.
    실제로 `recipes` 와 한국 레시피 표들은 `recipe_name` 컬럼을 똑같이 갖고 있지만
    값이 하나도 겹치지 않습니다(영어 대 한국어). 값을 봐야 가려집니다.
    """
    duplicates = {name for group in find_duplicate_groups(datasets) for name in group}
    result: dict[str, list[str]] = {dataset.name: [] for dataset in datasets}

    for left_index, left in enumerate(datasets):
        for right in datasets[left_index + 1 :]:
            if {left.name, right.name} <= duplicates and set(left.columns) == set(right.columns):
                continue
            shared = set(left.columns) & set(right.columns)
            if not shared:
                continue
            for column in sorted(shared):
                left_values = _column_values(left, column)
                right_values = _column_values(right, column)
                if not left_values or not right_values:
                    continue
                if _is_surrogate_id(left_values) or _is_surrogate_id(right_values):
                    continue
                overlap = len(left_values & right_values) / min(len(left_values), len(right_values))
                if overlap >= COMPANION_MIN_OVERLAP:
                    result[left.name].append(right.name)
                    result[right.name].append(left.name)
                    break

    return {name: sorted(set(values)) for name, values in result.items()}


def _targets_recipe(profile: DatasetProfile) -> bool:
    """이 프로파일이 레시피 본체를 다루는가. LLM 이 매긴 컬럼 대응으로 판단합니다."""
    return any((meaning.target_field or "").startswith("recipe.") for meaning in profile.column_meanings)


def apply(
    profiles: Sequence[DatasetProfile],
    datasets: Sequence[RawDataset],
) -> tuple[list[DatasetProfile], list[Adjustment]]:
    """프로파일에 제약을 적용하고 (고친 프로파일, 조정 기록) 을 돌려줍니다."""
    by_name = {dataset.name: dataset for dataset in datasets}
    companions = find_companions(datasets)
    adjustments: list[Adjustment] = []
    result: dict[str, DatasetProfile] = {profile.dataset: profile for profile in profiles}

    # --- 1. 존재하지 않는 컬럼 참조 제거 -----------------------------------
    # 형제 스키마를 프롬프트에 넣은 뒤로 다른 데이터셋 컬럼명을 끌어오는 일이 생겼습니다.
    # 2단계가 이 키로 그룹핑하면 바로 터지므로 여기서 걷어냅니다.
    for name, profile in result.items():
        dataset = by_name.get(name)
        if dataset is None:
            continue
        updates: dict[str, list[str]] = {}
        for field in ("group_by_columns", "entity_key_columns"):
            current: list[str] = getattr(profile, field)
            kept_columns = [column for column in current if column in dataset.columns]
            if kept_columns != current:
                dropped = [column for column in current if column not in dataset.columns]
                updates[field] = kept_columns
                adjustments.append(Adjustment(name, "unknown_column", f"{field} 에서 없는 컬럼 제거: {dropped}"))
        if updates:
            result[name] = profile.model_copy(update=updates)

    # --- 2. companion 을 실측값으로 교체 ------------------------------------
    for name, profile in result.items():
        measured = companions.get(name, [])
        if measured != profile.companion_datasets:
            result[name] = profile.model_copy(update={"companion_datasets": measured})
            adjustments.append(
                Adjustment(name, "companion", f"{profile.companion_datasets} -> {measured} (값 겹침 실측)")
            )

    # --- 3. storage_guideline 소스 단일화 -----------------------------------
    # 이미 슬롯 단위로 펼쳐진 소스가 있으면 그쪽만 씁니다. 넓은 원본은 같은 내용을
    # 중복 적재하게 되고, 자연키에 source_item_id 가 들어가서 UNIQUE 로도 안 막힙니다.
    storage_targets = [name for name, profile in result.items() if "storage_guideline" in profile.target_tables]
    slot_columns = {name: _slot_column(by_name[name]) for name in storage_targets if name in by_name}
    slot_ready = [name for name, column in slot_columns.items() if column is not None]
    if slot_ready:
        for name in storage_targets:
            if name in slot_ready:
                continue
            profile = result[name]
            kept: list[TargetTable] = [t for t in profile.target_tables if t != "storage_guideline"]
            updates_any: dict[str, object] = {"target_tables": kept or ["none"]}
            if not kept:
                updates_any["loadable"] = False
                updates_any["skip_reason"] = (
                    f"{', '.join(slot_ready)} 와 같은 원본이고 그쪽이 이미 슬롯 단위로 펼쳐져 있음"
                )
            result[name] = profile.model_copy(update=updates_any)
            adjustments.append(Adjustment(name, "storage_source", f"storage_guideline 제외 (슬롯 보유: {slot_ready})"))

    # --- 3-1. 슬롯 컬럼은 그룹 키가 될 수 없음 ------------------------------
    # 2단계 스키마(ExtractedStorageItem)는 **품목 하나에 슬롯 여러 개를 묶어서** 받습니다
    # (`rules: list[ExtractedStorageRule]`). 슬롯 컬럼을 그룹 키에 넣으면 한 품목이 슬롯 수만큼
    # 쪼개져 요청이 배로 늘고, 응답마다 rules 가 1개짜리로 옵니다.
    # 실제로 storage_guide 가 651품목 대신 1298건으로 갈렸습니다.
    for name in slot_ready:
        column = slot_columns[name]
        profile = result[name]
        if column is None or column not in profile.group_by_columns:
            continue
        kept_keys = [item for item in profile.group_by_columns if item != column]
        result[name] = profile.model_copy(update={"group_by_columns": kept_keys})
        adjustments.append(
            Adjustment(name, "slot_not_a_key", f"그룹 키에서 슬롯 컬럼 {column!r} 제거 (품목 단위로 묶음)")
        )

    # --- 4. recipe_ingredient 외래키 불변식 ---------------------------------
    # recipe_ingredient 는 recipe_id 를 참조합니다. recipe 없이 단독으로 적재할 수 없습니다.
    for name, profile in result.items():
        if "recipe_ingredient" in profile.target_tables and "recipe" not in profile.target_tables:
            tables: list[TargetTable] = ["recipe", *profile.target_tables]
            result[name] = profile.model_copy(update={"target_tables": tables})
            adjustments.append(Adjustment(name, "recipe_fk", "recipe_ingredient 가 있어 recipe 추가"))

    # --- 5. 검증된 짝은 한 덩어리로 취급 -------------------------------------
    # 사용자 결정(2026-09-09): 재료표와 단계표처럼 한 레시피가 두 표에 나뉜 경우
    # "반쪽이라 불완전" 으로 버리지 않고 짝을 묶어 적재합니다. LLM 은 두 번 다 제외했지만
    # 짝이라는 사실 자체는 위 2번에서 값으로 확인됩니다.
    for name, profile in result.items():
        if profile.loadable or not _targets_recipe(profile):
            continue
        # 짝이라는 것만으로는 부족합니다. **이 데이터셋의 그룹 키로 실제로 이어져야** 반쪽입니다.
        # 그러지 않으면 조리법 표처럼 recipe 필드를 하나 갖고 있고 우연히 겹치는 데이터까지
        # 적재 대상으로 끌려 올라옵니다.
        keys = set(profile.group_by_columns) or set(profile.entity_key_columns)
        partners = [
            other
            for other in profile.companion_datasets
            if other in result and other in by_name and keys & set(by_name[other].columns)
        ]
        if not keys or not partners:
            continue
        result[name] = profile.model_copy(
            update={
                "loadable": True,
                "target_tables": ["recipe", "recipe_ingredient"],
                "skip_reason": None,
            }
        )
        adjustments.append(Adjustment(name, "companion_pair", f"{partners} 와 짝이라 recipe/recipe_ingredient 로 적재"))

    # --- 6. 짝 그룹의 추출 주체는 하나 --------------------------------------
    # 2단계는 loadable 인 데이터셋마다 따로 요청을 만들고, 짝의 행은 companion 으로 끌어옵니다.
    # 그래서 짝 양쪽이 loadable 이면 **같은 엔티티가 두 번 추출됩니다.** recipe 자연키가
    # (source_type, source_recipe_id) 이고 source_type 기본값이 데이터셋 이름이라
    # 30개 레시피가 60행이 됩니다. 어느 쪽에서 뽑아도 companion 병합 결과는 같으므로
    # 하나만 남깁니다. 남지 않은 쪽도 companion 자료로는 계속 쓰입니다.
    for group in _connected_groups(companions, result):
        # 대응 필드가 많은 쪽(= 엔티티를 더 완전히 설명하는 쪽)을 고르고, 동률이면 이름 오름차순.
        primary = min(group, key=lambda name: (-_mapped_field_count(result[name]), name))
        for name in group:
            if name == primary:
                continue
            profile = result[name]
            reason = f"{primary} 와 같은 엔티티라 그쪽에서 추출합니다 (이 데이터셋은 companion 으로 쓰임)"
            result[name] = profile.model_copy(
                update={"loadable": False, "target_tables": ["none"], "skip_reason": reason}
            )
            adjustments.append(Adjustment(name, "companion_primary", f"추출 주체를 {primary} 로 모음 (중복 추출 방지)"))

    ordered = sorted(result.values(), key=lambda item: item.dataset)
    return ordered, adjustments


def _mapped_field_count(profile: DatasetProfile) -> int:
    """타깃 필드에 실제로 대응된 컬럼 수. 짝 중 누가 더 완전한지 가리는 기준입니다."""
    return sum(1 for meaning in profile.column_meanings if meaning.target_field)


def _connected_groups(
    companions: dict[str, list[str]],
    profiles: dict[str, DatasetProfile],
) -> list[list[str]]:
    """짝 관계로 이어진 덩어리 중, 적재 대상이 둘 이상이고 타깃 테이블이 겹치는 것만."""
    seen: set[str] = set()
    groups: list[list[str]] = []
    for start in sorted(companions):
        if start in seen:
            continue
        stack = [start]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(other for other in companions.get(current, []) if other not in seen)

        loadable = [name for name in sorted(component) if name in profiles and profiles[name].loadable]
        if len(loadable) < 2:
            continue
        tables = [set(profiles[name].target_tables) - {"none"} for name in loadable]
        if not set.intersection(*tables):
            continue
        groups.append(loadable)
    return groups


def render(adjustments: Sequence[Adjustment]) -> str:
    """사람이 읽을 조정 요약."""
    if not adjustments:
        return "제약 조정 없음 (LLM 판단 그대로)"
    lines = [f"제약 조정 {len(adjustments)}건:"]
    lines.extend(f"  {item.render()}" for item in adjustments)
    return "\n".join(lines)
