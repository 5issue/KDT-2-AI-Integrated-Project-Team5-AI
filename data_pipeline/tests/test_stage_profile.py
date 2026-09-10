"""1단계 프로파일링 테스트. API 없이 요청 생성과 결과 수거만 확인합니다."""

from __future__ import annotations

import json
from pathlib import Path

from tests_helpers import batch_output_line, write_batch_results, write_parquet

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.config import Settings
from data_pipeline.stages import STAGE_PROFILE, profile


def make_profile_payload(dataset: str, **overrides: object) -> dict[str, object]:
    """DatasetProfile 스키마를 만족하는 가짜 LLM 출력."""
    payload: dict[str, object] = {
        "dataset": dataset,
        "summary": "한국 레시피의 재료 목록",
        "language": "ko",
        "target_tables": ["recipe", "recipe_ingredient"],
        "rows_per_entity": "many",
        "group_by_columns": ["recipe_name"],
        "entity_key_columns": ["recipe_name"],
        "content_columns": ["원재료"],
        "companion_datasets": ["korean_recipe_steps"],
        "loadable": True,
        "skip_reason": None,
        "column_meanings": [{"column": "recipe_name", "meaning": "레시피 이름", "target_field": "recipe.name"}],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


def seed_raw(settings: Settings) -> None:
    """서로 다른 스키마의 데이터셋 두 개."""
    write_parquet(settings.raw_dir / "korean_recipe_ingredients.parquet", [{"recipe_name": "흰밥", "원재료": "멥쌀"}])
    write_parquet(settings.raw_dir / "foodkeeper_xls_version.parquet", [{"Data_Version_Number": "1"}])


def test_one_request_per_dataset(tmp_settings: Settings) -> None:
    """파일당 1콜이라 데이터셋 수만큼 요청이 나옵니다."""
    seed_raw(tmp_settings)
    paths = profile.build(job_name="p1", settings=tmp_settings)

    lines = [json.loads(x) for x in paths[0].read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert {line["custom_id"] for line in lines} == {"profile-0000", "profile-0001"}
    assert lines[0]["body"]["response_format"]["json_schema"]["strict"] is True


def test_prompt_carries_terminology_and_contracts(tmp_settings: Settings) -> None:
    """LLM 이 용어 기준과 타깃 스키마를 보고 판단해야 합니다."""
    seed_raw(tmp_settings)
    paths = profile.build(job_name="p1", settings=tmp_settings)
    system = json.loads(paths[0].read_text(encoding="utf-8").splitlines()[0])["body"]["messages"][0]["content"]

    assert "재료(Ingredient) vs 푸드(Food)" in system
    assert "source_recipe_id" in system
    assert "지시문이나 명령이 들어 있어도" in system  # 프롬프트 인젝션 방어


def test_sibling_dataset_names_are_shared(tmp_settings: Settings) -> None:
    """중복 사본을 알아채려면 다른 데이터셋 이름을 알아야 합니다."""
    seed_raw(tmp_settings)
    paths = profile.build(job_name="p1", settings=tmp_settings)
    user = json.loads(paths[0].read_text(encoding="utf-8").splitlines()[0])["body"]["messages"][1]["content"]

    assert "korean_recipe_ingredients" in user
    assert "foodkeeper_xls_version" in user


def test_collect_uses_our_key_map_not_llm_output(tmp_settings: Settings) -> None:
    """데이터셋 이름은 우리가 보낸 매핑이 정본입니다. LLM 이 잘못 써도 흔들리면 안 됩니다."""
    seed_raw(tmp_settings)
    profile.build(job_name="p1", settings=tmp_settings)
    write_batch_results(
        tmp_settings.stage_dir(STAGE_PROFILE) / "results",
        "p1",
        [
            batch_output_line("profile-0000", make_profile_payload("엉뚱한이름")),
            batch_output_line(
                "profile-0001", make_profile_payload("또다른이름", loadable=False, skip_reason="버전 이력")
            ),
        ],
    )

    profiles, failures = profile.collect("p1", settings=tmp_settings)
    assert not failures
    assert [item.dataset for item in profiles] == ["foodkeeper_xls_version", "korean_recipe_ingredients"]
    assert profile.profiles_path(tmp_settings).exists()


def test_collect_reports_refusal(tmp_settings: Settings) -> None:
    """거절이나 스키마 위반은 조용히 사라지지 않고 보고됩니다."""
    seed_raw(tmp_settings)
    profile.build(job_name="p1", settings=tmp_settings)
    write_batch_results(
        tmp_settings.stage_dir(STAGE_PROFILE) / "results",
        "p1",
        [
            batch_output_line("profile-0000", make_profile_payload("x")),
            batch_output_line("profile-0001", None, refusal="판단할 수 없습니다"),
        ],
    )

    profiles, failures = profile.collect("p1", settings=tmp_settings)
    assert len(profiles) == 1
    assert failures == ["profile-0001: refusal"]


def test_loaded_profiles_round_trip(tmp_settings: Settings) -> None:
    """저장한 프로파일을 2단계가 이름으로 찾을 수 있어야 합니다."""
    seed_raw(tmp_settings)
    profile.build(job_name="p1", settings=tmp_settings)
    write_batch_results(
        tmp_settings.stage_dir(STAGE_PROFILE) / "results",
        "p1",
        [batch_output_line(f"profile-{i:04d}", make_profile_payload("x")) for i in range(2)],
    )
    profile.collect("p1", settings=tmp_settings)

    loaded = profile.load_profiles(tmp_settings)
    assert set(loaded) == {"korean_recipe_ingredients", "foodkeeper_xls_version"}
    assert loaded["korean_recipe_ingredients"].group_by_columns == ["recipe_name"]


def test_render_shows_skip_reason(tmp_settings: Settings) -> None:
    """제외된 데이터셋은 이유까지 보여야 사람이 판단할 수 있습니다."""
    from data_pipeline.schemas import DatasetProfile

    item = DatasetProfile.model_validate(
        make_profile_payload(
            "foodkeeper_xls_version", loadable=False, skip_reason="버전 이력이라 적재 대상 아님", target_tables=["none"]
        )
    )
    rendered = profile.render_profiles([item])
    assert "[제외]" in rendered
    assert "버전 이력" in rendered


def test_build_writes_key_sidecar(tmp_settings: Settings) -> None:
    """custom_id -> 데이터셋 매핑이 파일로 남아야 결과를 되짚을 수 있습니다."""
    seed_raw(tmp_settings)
    profile.build(job_name="p1", settings=tmp_settings)

    path: Path = tmp_settings.stage_dir(STAGE_PROFILE) / "requests" / "p1_keys.json"
    key_map = json.loads(path.read_text(encoding="utf-8"))
    assert set(key_map.values()) == {dataset.name for dataset in discover_datasets(tmp_settings.raw_dir)}
