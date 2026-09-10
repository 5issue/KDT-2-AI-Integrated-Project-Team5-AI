"""1단계: 데이터셋 프로파일링.

raw 파일은 CSV/PDF/크롤링 텍스트를 겨우 parquet 으로 뽑아낸 것이라 스키마가 통일되어
있지 않습니다. 그래서 파이썬에 컬럼명을 적어 두는 대신, 파일마다 한 번씩 LLM 에게
"이게 뭐고, 어느 테이블로 가고, 한 엔티티가 몇 행인지" 를 묻습니다.

파일당 1콜이라 13개 파일이면 13콜입니다. 판단 근거로 용어 기준표와 실제 타깃 테이블
계약, 그리고 같은 폴더에 있는 다른 데이터셋 이름들을 함께 줍니다(중복 데이터 탐지용).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_pipeline.batch.client import BatchRunner, build_chat_request
from data_pipeline.batch.raw_source import RawDataset, discover_datasets, render_dataset_brief
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import SAFETY_RULES, TARGET_TABLE_CONTRACTS, load_terminology
from data_pipeline.schemas import DatasetProfile
from data_pipeline.stages import STAGE_PROFILE

PROFILE_SYSTEM_TEMPLATE = """\
너는 신선식품 도메인 데이터 엔지니어다. 출처가 제각각인 데이터셋 하나를 보고
관계형 DB 에 어떻게 적재할지 판단한다.

{safety}

{terminology}

{contracts}

판단 지침:
- target_tables 는 위 계약에 있는 테이블만 고른다. 어디에도 안 맞으면 ["none"] 과
  loadable=false, skip_reason 을 적는다.
- 레시피처럼 recipe 와 recipe_ingredient 를 동시에 만드는 데이터셋은 둘 다 적는다.
- rows_per_entity: 한 행이 한 엔티티면 "one", 한 엔티티가 여러 행에 흩어져 있으면 "many".
  many 면 group_by_columns 에 묶는 기준 컬럼을 반드시 적는다.
- entity_key_columns 는 그 엔티티를 다시 찾을 수 있는 자연키다. 없으면 이름 컬럼이라도 적는다.
- 버전 이력, 컬럼 사전, 변환 실패 로그처럼 적재 대상이 아닌 것은 loadable=false 로 둔다.
- 같은 폴더의 다른 데이터셋과 내용이 겹쳐 보이면(같은 원본의 타입만 다른 사본 등)
  더 다루기 쉬운 쪽만 loadable=true 로 두고, 나머지는 skip_reason 에 어느 것과 겹치는지 적는다.
"""

PROFILE_USER_TEMPLATE = """\
같은 폴더에 있는 데이터셋 목록: {siblings}

<data>
{brief}
</data>
"""


def build_system_prompt(settings: Settings | None = None) -> str:
    """1단계 시스템 프롬프트."""
    settings = settings or get_settings()
    return PROFILE_SYSTEM_TEMPLATE.format(
        safety=SAFETY_RULES,
        terminology=load_terminology(settings.terminology_path),
        contracts=TARGET_TABLE_CONTRACTS,
    )


def build_requests(
    datasets: list[RawDataset],
    *,
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """데이터셋마다 요청 하나. custom_id -> 데이터셋 이름 매핑도 함께 돌려줍니다."""
    settings = settings or get_settings()
    system = build_system_prompt(settings)
    siblings = ", ".join(dataset.name for dataset in datasets)

    requests: list[dict[str, Any]] = []
    key_map: dict[str, str] = {}
    for index, dataset in enumerate(datasets):
        custom_id = f"profile-{index:04d}"
        key_map[custom_id] = dataset.name
        user = PROFILE_USER_TEMPLATE.format(
            siblings=siblings,
            brief=render_dataset_brief(dataset, limit=settings.profile_sample_rows),
        )
        requests.append(
            build_chat_request(
                custom_id,
                system=system,
                user=user,
                model=settings.openai_batch_model,
                schema_name="dataset_profile",
                schema_model=DatasetProfile,
            )
        )
    return requests, key_map


def build(raw_path: Path | None = None, *, job_name: str, settings: Settings | None = None) -> list[Path]:
    """raw 경로를 훑어 1단계 요청 파일을 만듭니다."""
    settings = settings or get_settings()
    datasets = discover_datasets(raw_path or settings.raw_dir)
    requests, key_map = build_requests(datasets, settings=settings)

    runner = BatchRunner(STAGE_PROFILE, settings)
    paths = runner.write_requests(requests, job_name=job_name)
    (runner.requests_dir / f"{job_name}_keys.json").write_text(
        json.dumps(key_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths


def profiles_path(settings: Settings | None = None) -> Path:
    """1단계 중간 산출물 경로."""
    settings = settings or get_settings()
    return settings.artifacts_dir / "profiles.json"


def collect(job_name: str, *, settings: Settings | None = None) -> tuple[list[DatasetProfile], list[str]]:
    """결과를 검증해 profiles.json 으로 떨어뜨립니다. (프로파일 목록, 실패 사유) 를 돌려줍니다."""
    settings = settings or get_settings()
    runner = BatchRunner(STAGE_PROFILE, settings)
    key_map = json.loads((runner.requests_dir / f"{job_name}_keys.json").read_text(encoding="utf-8"))

    outcome = runner.parse(job_name, DatasetProfile)
    profiles: list[DatasetProfile] = []
    for custom_id, model in outcome.records:
        assert isinstance(model, DatasetProfile)
        # 데이터셋 이름은 LLM 출력이 아니라 우리가 보낸 매핑을 정본으로 씁니다.
        profiles.append(model.model_copy(update={"dataset": key_map.get(custom_id, model.dataset)}))
    profiles.sort(key=lambda item: item.dataset)

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    profiles_path(settings).write_text(
        json.dumps([profile.model_dump() for profile in profiles], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = [f"{failure.custom_id}: {failure.reason}" for failure in outcome.failures]
    return profiles, failures


def load_profiles(settings: Settings | None = None) -> dict[str, DatasetProfile]:
    """저장된 프로파일을 데이터셋 이름으로 찾을 수 있게 읽습니다."""
    path = profiles_path(settings)
    if not path.exists():
        raise FileNotFoundError(f"프로파일이 없습니다: {path.name}. 1단계를 먼저 끝내세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item["dataset"]: DatasetProfile.model_validate(item) for item in payload}


def render_profiles(profiles: list[DatasetProfile]) -> str:
    """사람이 읽을 요약."""
    lines = []
    for profile in profiles:
        mark = "적재" if profile.loadable else "제외"
        tables = ", ".join(profile.target_tables)
        lines.append(f"[{mark}] {profile.dataset:32} -> {tables:34} (확신도 {profile.confidence:.2f})")
        lines.append(f"        {profile.summary[:100]}")
        if profile.rows_per_entity == "many":
            lines.append(f"        그룹 키: {profile.group_by_columns}")
        if not profile.loadable and profile.skip_reason:
            lines.append(f"        제외 사유: {profile.skip_reason}")
    return "\n".join(lines)
