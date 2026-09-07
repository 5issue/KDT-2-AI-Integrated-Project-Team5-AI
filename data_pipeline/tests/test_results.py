"""Batch API 결과 파싱/검증 테스트."""

from __future__ import annotations

from pathlib import Path

from data_pipeline.batch.results import parse_recipe_results


def test_parse_sample_output_splits_success_and_failure(samples_dir: Path) -> None:
    """정상 2건, 거절 1건, 배치 에러 1건을 각각 분류합니다."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")

    assert outcome.total == 4
    assert [record.source_id for record in outcome.records] == ["sample-001", "sample-002"]
    assert {failure.reason for failure in outcome.failures} == {"refusal", "batch_error"}


def test_parsed_record_maps_to_schema_fields(samples_dir: Path) -> None:
    """파싱 결과가 recipe 컬럼에 대응하는 값으로 들어옵니다."""
    outcome = parse_recipe_results(samples_dir / "batch_output.sample.jsonl")
    kimchi = outcome.records[0].recipe

    assert kimchi.name == "김치찌개"
    assert kimchi.difficulty == "EASY"
    assert kimchi.servings == 2
    assert kimchi.nutrition is not None
    assert kimchi.nutrition.calories_kcal == 420
    assert [item.normalized_name for item in kimchi.ingredients][:2] == ["김치", "돼지고기"]

    egg_rice = outcome.records[1].recipe
    assert egg_rice.prep_time_min is None  # 원문에 없으면 null 을 유지해야 합니다
    assert egg_rice.nutrition is None
