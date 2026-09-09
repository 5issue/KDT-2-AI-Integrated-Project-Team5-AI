"""2단계: 레코드를 타깃 테이블 모양으로 추출.

1단계 프로파일이 "이 데이터셋은 어느 테이블이고 한 엔티티가 몇 행인지" 를 알려주므로,
여기서는 그 판단대로 행을 묶어 엔티티 단위로 LLM 에게 보냅니다. 컬럼명은 프로파일이 준
해석(column_meanings)을 프롬프트에 같이 넣어 주고, 파이썬은 어떤 컬럼도 이름으로 찾지 않습니다.

영어 데이터도 여기서 한국어로 정규화합니다. 재료 마스터가 한국어라 3단계 매칭이
가능하려면 이 단계에서 언어를 맞춰야 합니다. 원문은 name_original 로 보존합니다.

프로파일에 companion_datasets 가 있으면(예: 레시피 재료 표와 조리 단계 표) 같은 엔티티의
행을 함께 넣어 줍니다. 한 번에 보면 더 정확하게 뽑힙니다.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from data_pipeline.batch.client import BatchRunner, build_chat_request
from data_pipeline.batch.raw_source import RawDataset, discover_datasets, iter_records
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import SAFETY_RULES, TARGET_TABLE_CONTRACTS, load_terminology
from data_pipeline.schemas import DatasetProfile, ExtractedRecipe, ExtractedStorageItem
from data_pipeline.stages import STAGE_EXTRACT

# 동반 데이터셋을 통째로 메모리에 올리므로 상한을 둡니다.
COMPANION_ROW_LIMIT = 200_000

RECIPE_SYSTEM_TEMPLATE = """\
너는 신선식품 커머스의 레시피 데이터를 관계형 DB 에 넣기 위해 구조화하는 파서다.

{safety}

{terminology}

{contracts}

이 데이터셋에 대한 사전 분석:
{profile}

추가 지침:
- 재료명과 레시피명은 **한국어**로 정규화한다. 원문이 영어면 한국어로 옮기고 원표기는
  name_original 에 남긴다. 재료 마스터가 한국어라 이후 매칭에 한국어가 필요하다.
- normalized_name 은 수식어/브랜드/손질 상태를 뺀 한국어 기본형으로 적는다.
  예: 'unsalted butter' -> '버터', '½ cup chopped onion' -> '양파', '다진 마늘' -> '마늘'
- is_raw_material 은 용어 기준의 원재료 정의로 판단한다. 밀가루/설탕/생고기는 원재료,
  마요네즈/간장/훈제육 같은 가공품은 원재료가 아니다.
- purpose 와 tags 는 용어 기준의 용도/TPO 분류에서 고른다. 해당 없으면 비운다.
- 조리 시간이나 영양정보가 원문에 없으면 지어내지 말고 null 로 둔다.
- **단위(unit)도 한국어로 통일한다.** 이름만 옮기고 단위를 영어로 두면 한 테이블에
  'tablespoons' 와 '큰술' 이 섞인다. 아래처럼 옮긴다.
    tablespoon(s) -> 큰술 / teaspoon(s) -> 작은술 / cup(s) -> 컵 / pound(s) -> 파운드
    ounce(s) -> 온스 / clove(s) -> 쪽 / stalk(s) -> 대 / can -> 캔 / pinch -> 꼬집
    slice(s) -> 장 / sheet(s) -> 장 / bunch -> 단
  g, kg, ml, L 처럼 국제단위는 그대로 둔다. 단위가 없으면 null 로 둔다.
- **description 에 URL 이나 출처 표기를 넣지 않는다.** 요리가 어떤 음식인지 설명하는
  두세 문장만 쓴다. 원문에 그런 설명이 없으면 null 로 둔다. 링크만 있는 경우도 null 이다.
- **steps 의 instruction 에서 원문의 번호 접두사를 뗀다.** '1.', '2)', '단계 3' 같은
  표기는 빼고 내용만 남긴다. 순서는 step_no 가 갖는다. 그대로 두면 화면에 번호가
  두 번 나온다. 원문의 단계 구분(예: '준비하기', '조리하기')이 유용하면 내용 앞에
  붙여도 되지만 숫자는 뺀다.
"""

STORAGE_SYSTEM_TEMPLATE = """\
너는 식품 보관 기준 데이터를 관계형 DB 에 넣기 위해 구조화하는 파서다.

{safety}

{terminology}

{contracts}

이 데이터셋에 대한 사전 분석:
{profile}

추가 지침:
- 한 품목이 여러 보관 슬롯(냉장/냉동/실온, 구매일 기준, 개봉 후 등)을 가진다.
  주어진 행들에서 확인되는 슬롯만 rules 에 넣는다. 없는 슬롯을 만들어내지 않는다.
- duration_text 는 비울 수 없다. 수치가 있으면 '1-2 개월' 처럼 적고, 수치가 없고
  'Or until best-by date.' 같은 문구만 있으면 그 뜻을 한국어로 옮겨 적는다.
- food_name_ko 와 normalized_name 은 한국어로 적는다. 재료 마스터가 한국어다.
  예: 'Butter' -> '버터', 'Turkey bacon' -> '칠면조 베이컨' / 기본형 '베이컨'
- storage_tips 는 한국어로 옮긴다. 없으면 null.
"""

USER_TEMPLATE = """\
엔티티 키: {entity_key}

<data>
{rows}
</data>
"""


@dataclass(slots=True)
class Entity:
    """한 엔티티(레시피 1건, 품목 1건)를 이루는 원본 행 묶음."""

    dataset: str
    key: str
    rows: list[dict[str, Any]]
    companions: dict[str, list[dict[str, Any]]]


def _key_of(payload: dict[str, Any], columns: Sequence[str]) -> str:
    """지정된 컬럼 값들로 엔티티 키 문자열을 만듭니다."""
    parts = [str(payload.get(column, "")).strip() for column in columns]
    return "|".join(part for part in parts if part)


def _companion_index(dataset: RawDataset, columns: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """동반 데이터셋을 엔티티 키로 인덱싱합니다."""
    if dataset.row_count > COMPANION_ROW_LIMIT:
        raise ValueError(f"동반 데이터셋이 너무 큽니다: {dataset.name} ({dataset.row_count}행)")
    index: dict[str, list[dict[str, Any]]] = {}
    for record in iter_records(dataset):
        index.setdefault(_key_of(record.payload, columns), []).append(record.payload)
    return index


def iter_entities(
    dataset: RawDataset,
    profile: DatasetProfile,
    *,
    companions: dict[str, tuple[RawDataset, DatasetProfile]] | None = None,
    limit: int = 0,
) -> Iterator[Entity]:
    """프로파일의 판단대로 행을 엔티티로 묶습니다."""
    group_columns = profile.group_by_columns or profile.entity_key_columns

    companion_indexes: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for name, (companion_dataset, companion_profile) in (companions or {}).items():
        shared = [
            column
            for column in group_columns
            if column in companion_dataset.columns
            and column in (companion_profile.group_by_columns or companion_profile.entity_key_columns)
        ]
        if shared:
            companion_indexes[name] = _companion_index(companion_dataset, shared)

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for record in iter_records(dataset):
        key = _key_of(record.payload, group_columns) if group_columns else ""
        if not key:
            key = f"row-{record.row_index}"
        if profile.rows_per_entity == "one":
            key = f"{key}#{record.row_index}" if key in grouped else key
        grouped.setdefault(key, []).append(record.payload)

    for index, (key, rows) in enumerate(grouped.items()):
        if limit and index >= limit:
            return
        yield Entity(
            dataset=dataset.name,
            key=key,
            rows=rows,
            companions={name: companion_index.get(key, []) for name, companion_index in companion_indexes.items()},
        )


def target_model(profile: DatasetProfile) -> type[BaseModel] | None:
    """프로파일이 고른 테이블에 맞는 추출 스키마."""
    if "storage_guideline" in profile.target_tables:
        return ExtractedStorageItem
    if "recipe" in profile.target_tables:
        return ExtractedRecipe
    return None


def _profile_brief(profile: DatasetProfile) -> str:
    """프롬프트에 넣을 프로파일 요약."""
    meanings = "\n".join(
        f"  - {item.column}: {item.meaning}" + (f" -> {item.target_field}" if item.target_field else "")
        for item in profile.column_meanings
    )
    return (
        f"요약: {profile.summary}\n"
        f"언어: {profile.language}\n"
        f"행 단위: {profile.rows_per_entity} (그룹 키 {profile.group_by_columns})\n"
        f"자연키 컬럼: {profile.entity_key_columns}\n"
        f"컬럼 해석:\n{meanings}"
    )


def build_requests(
    entities: Sequence[Entity],
    profile: DatasetProfile,
    *,
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """엔티티마다 요청 하나."""
    settings = settings or get_settings()
    model = target_model(profile)
    if model is None:
        return [], {}

    template = STORAGE_SYSTEM_TEMPLATE if model is ExtractedStorageItem else RECIPE_SYSTEM_TEMPLATE
    schema_name = "extracted_storage_item" if model is ExtractedStorageItem else "extracted_recipe"
    system = template.format(
        safety=SAFETY_RULES,
        terminology=load_terminology(settings.terminology_path),
        contracts=TARGET_TABLE_CONTRACTS,
        profile=_profile_brief(profile),
    )

    slug = "".join(char if char.isalnum() else "-" for char in profile.dataset)[:28]
    requests: list[dict[str, Any]] = []
    key_map: dict[str, dict[str, Any]] = {}

    for index, entity in enumerate(entities):
        custom_id = f"x-{slug}-{index:06d}"
        key_map[custom_id] = {"dataset": entity.dataset, "entity_key": entity.key, "rows": len(entity.rows)}
        payload: dict[str, Any] = {"main": entity.rows}
        payload.update({name: rows for name, rows in entity.companions.items() if rows})
        requests.append(
            build_chat_request(
                custom_id,
                system=system,
                user=USER_TEMPLATE.format(
                    entity_key=entity.key,
                    rows=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                ),
                model=settings.openai_batch_model,
                schema_name=schema_name,
                schema_model=model,
            )
        )
    return requests, key_map


def build(
    *,
    job_name: str,
    raw_path: Path | None = None,
    profiles: dict[str, DatasetProfile],
    settings: Settings | None = None,
) -> tuple[list[Path], dict[str, int]]:
    """적재 가능한 데이터셋 전부에 대해 2단계 요청 파일을 만듭니다."""
    settings = settings or get_settings()
    datasets = {dataset.name: dataset for dataset in discover_datasets(raw_path or settings.raw_dir)}

    all_requests: list[dict[str, Any]] = []
    key_map: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}

    for name, profile in sorted(profiles.items()):
        if not profile.loadable or target_model(profile) is None or name not in datasets:
            continue
        companions = {
            other: (datasets[other], profiles[other])
            for other in profile.companion_datasets
            if other in datasets and other in profiles and other != name
        }
        entities = list(
            iter_entities(datasets[name], profile, companions=companions, limit=settings.extract_max_entities)
        )
        requests, keys = build_requests(entities, profile, settings=settings)
        all_requests.extend(requests)
        key_map.update(keys)
        counts[name] = len(requests)

    if not all_requests:
        raise ValueError("2단계에서 만들 요청이 없습니다. 1단계 프로파일의 loadable 을 확인하세요.")

    runner = BatchRunner(STAGE_EXTRACT, settings)
    paths = runner.write_requests(all_requests, job_name=job_name)
    (runner.requests_dir / f"{job_name}_keys.json").write_text(
        json.dumps(key_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths, counts


def records_path(dataset: str, settings: Settings | None = None) -> Path:
    """데이터셋별 2단계 중간 산출물 경로."""
    settings = settings or get_settings()
    return settings.artifacts_dir / "records" / f"{dataset}.jsonl"


def collect(
    job_name: str,
    *,
    profiles: dict[str, DatasetProfile],
    settings: Settings | None = None,
) -> tuple[dict[str, int], list[str]]:
    """결과를 검증해 데이터셋별 JSONL 로 떨어뜨립니다."""
    settings = settings or get_settings()
    runner = BatchRunner(STAGE_EXTRACT, settings)
    key_map = json.loads((runner.requests_dir / f"{job_name}_keys.json").read_text(encoding="utf-8"))

    # 한 배치 안에 레시피용과 보관기준용 요청이 섞여 있습니다.
    # custom_id 로 어느 스키마인지 되짚어 검증합니다.
    schema_by_id: dict[str, type[BaseModel]] = {}
    for custom_id, meta in key_map.items():
        profile = profiles.get(meta["dataset"])
        model = target_model(profile) if profile else None
        if model is not None:
            schema_by_id[custom_id] = model

    by_dataset: dict[str, list[dict[str, Any]]] = {meta["dataset"]: [] for meta in key_map.values()}
    failures: list[str] = []

    for custom_id, content, failure in runner.iter_contents(job_name):
        meta = key_map.get(custom_id)
        model = schema_by_id.get(custom_id)
        if meta is None or model is None:
            failures.append(f"{custom_id}: 요청 매핑을 찾지 못했습니다")
            continue
        if failure is not None:
            failures.append(f"{custom_id}: {failure.reason}")
            continue
        assert content is not None
        try:
            parsed = model.model_validate_json(content)
        except ValidationError as exc:
            failures.append(f"{custom_id}: schema_validation {str(exc)[:120]}")
            continue
        payload = parsed.model_dump()
        payload["_entity_key"] = meta["entity_key"]
        payload["_dataset"] = meta["dataset"]
        by_dataset[meta["dataset"]].append(payload)

    counts: dict[str, int] = {}
    (settings.artifacts_dir / "records").mkdir(parents=True, exist_ok=True)
    for dataset, rows in by_dataset.items():
        path = records_path(dataset, settings)
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        counts[dataset] = len(rows)
    return counts, failures


def load_records(dataset: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    """저장된 2단계 산출물을 읽습니다."""
    path = records_path(dataset, settings)
    if not path.exists():
        raise FileNotFoundError(f"2단계 산출물이 없습니다: {path.name}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
