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
from data_pipeline.batch.raw_source import (
    RawDataset,
    discover_datasets,
    render_dataset_brief,
    render_sibling_catalog,
)
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import SAFETY_RULES, TARGET_TABLE_CONTRACTS, load_terminology
from data_pipeline.schemas import DatasetProfile
from data_pipeline.stages import STAGE_PROFILE, constraints

PROFILE_SYSTEM_TEMPLATE = """\
너는 신선식품 도메인 데이터 엔지니어다. 출처가 제각각인 데이터셋 하나를 보고
관계형 DB 에 어떻게 적재할지 판단한다.

{safety}

{terminology}

{contracts}

판단 지침:
- target_tables 는 위 계약에 있는 테이블만 고른다. 어디에도 안 맞으면 ["none"] 과
  loadable=false, skip_reason 을 적는다.
- entity_key_columns 는 그 엔티티를 다시 찾을 수 있는 자연키다. 없으면 이름 컬럼이라도 적는다.
- **group_by_columns 와 entity_key_columns 에는 아래 <data> 에 실제로 있는 컬럼 이름만 적는다.**
  타깃 테이블 컬럼명(source_item_id, source_slot, ingredient_id 등)을 여기 적으면 안 된다.
  타깃 필드와의 대응은 column_meanings 에서만 다룬다.
- 버전 이력, 컬럼 사전, 변환 실패 로그처럼 적재 대상이 아닌 것은 loadable=false 로 둔다.

[엔티티와 group_by_columns]
- 엔티티는 "타깃 테이블의 한 행"이 아니라 **현실의 대상 하나**다.
  레시피 데이터면 엔티티는 레시피 하나이고, 보관 데이터면 품목 하나다.
- rows_per_entity: 한 행이 한 엔티티면 "one", 한 엔티티가 여러 행에 흩어져 있으면 "many".
- group_by_columns 에는 **엔티티를 식별하는 컬럼만** 넣는다. 재료명·단계명처럼 한 엔티티
  안에서 행마다 달라지는 하위 항목 컬럼을 넣으면 안 된다. 그것을 넣으면 그룹 하나가
  하위 항목 하나가 되어 엔티티가 쪼개진다.
  예) 레시피별 재료 목록에서 group_by_columns 는 ["recipe_name"] 이다.
      ["recipe_name", "재료명"] 은 틀렸다.

[중복 사본 판정 — 아래 형제 데이터셋 목록을 반드시 비교할 것]
- 컬럼 구성이 사실상 같고 행 수도 같은 형제가 있으면 **같은 원본의 사본**이다.
  이름이 달라도(예: `_xls_` 가 붙은 쪽) 사본이다. 타입만 다른 경우가 흔하다.
- 사본이 여럿이면 **한쪽만 loadable=true** 로 둔다. 고르는 기준은 이 순서다.
  1) 이미 타깃 테이블 모양으로 펼쳐져 있는 것(한 행이 한 결과 행) 을 우선한다.
     넓은 표(컬럼 수십 개에 항목이 흩어진 형태)보다 낫다.
  2) 타깃 테이블이 요구하는 열거형 값을 원본이 그대로 갖고 있으면 그쪽을 우선한다.
- 탈락시킨 쪽은 loadable=false 로 두고 skip_reason 에 **어느 데이터셋과 겹치는지 이름을 적는다.**

[companion_datasets — 반드시 채울 것]
- 형제 중에 **같은 자연키를 공유하는** 데이터셋이 있으면 companion_datasets 에 그 이름을 적는다.
  한 엔티티의 정보가 두 표에 나뉘어 있다는 뜻이고, 2단계에서 같이 봐야 한다.
  예) 레시피 재료 표와 레시피 조리단계 표가 같은 레시피 이름을 공유하면 서로 companion 이다.
- companion 관계는 **양쪽 다** 기재한다. 어느 쪽을 보고 있든 상대를 적는다.
- 한 엔티티의 반쪽이라서 혼자서는 불완전한 데이터셋이라도, companion 과 합치면 적재가 되면
  loadable=false 로 버리지 말고 companion_datasets 를 채운 채 loadable=true 로 둔다.

[테이블 선택]
- 레시피 데이터에 **재료 목록 컬럼이 있으면 recipe 와 recipe_ingredient 를 둘 다 고른다.**
  재료가 한 덩어리 문자열로 뭉쳐 있어도, 수량이나 단위가 빠져 있어도 마찬가지다.
  그런 것은 2단계에서 쪼개고 없는 값은 null 로 둔다. 지금 판단할 것은 "재료 정보가
  들어 있는가" 뿐이다. 재료 컬럼을 보고도 recipe 만 고르면 재료가 통째로 유실된다.
- 재료 목록 컬럼은 column_meanings 에서 recipe_ingredient 의 필드로 대응시킨다.
- **조리 순서 컬럼이 있으면 recipe_step 도 함께 고른다.** 순서가 한 덩어리 문자열이든
  단계마다 한 행이든 마찬가지다. 2단계에서 단계로 쪼갠다.
  조리 순서를 `recipe.description` 으로 대응시키지 않는다. description 은 요리 소개이고,
  순서는 recipe_step 이 받는다. 둘을 섞으면 화면이 단계를 표시하지 못한다.
- 사진 URL 컬럼이 있으면 `recipe.image_url` 로 대응시킨다.
- storage_guideline 은 source_slot 을 위 9개 열거형 중 하나로 **확정할 수 있는 구조화된
  데이터에만** 쓴다. "서늘한 곳에 보관" 같은 자유 서술 문장만 있고 슬롯·기간을 특정할 수
  없으면 storage_guideline 을 고르지 않는다. 지어내면 CHECK 제약에서 실패한다.
"""

PROFILE_USER_TEMPLATE = """\
같은 폴더에 있는 다른 데이터셋 (이름, 행 수, 컬럼):
{siblings}

아래가 이번에 판단할 데이터셋이다.

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

    requests: list[dict[str, Any]] = []
    key_map: dict[str, str] = {}
    for index, dataset in enumerate(datasets):
        custom_id = f"profile-{index:04d}"
        key_map[custom_id] = dataset.name
        user = PROFILE_USER_TEMPLATE.format(
            # 이름만 주면 중복 사본도 companion 도 알아낼 수 없어 스키마까지 넣습니다.
            siblings=render_sibling_catalog(datasets, exclude=dataset.name),
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


def collect(
    job_name: str,
    *,
    settings: Settings | None = None,
    raw_path: Path | None = None,
) -> tuple[list[DatasetProfile], list[str], list[constraints.Adjustment]]:
    """결과를 검증해 profiles.json 으로 떨어뜨립니다.

    LLM 판단을 그대로 쓰지 않고 `constraints.apply()` 로 결정적 제약을 한 번 통과시킵니다.
    무엇이 고쳐졌는지는 조정 기록으로 돌려주어 사람이 검토할 수 있게 합니다.
    """
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

    datasets = discover_datasets(raw_path or settings.raw_dir)
    profiles, adjustments = constraints.apply(profiles, datasets)

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    profiles_path(settings).write_text(
        json.dumps([profile.model_dump() for profile in profiles], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = [f"{failure.custom_id}: {failure.reason}" for failure in outcome.failures]
    return profiles, failures, adjustments


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
