"""2단계 추출 테스트. 프로파일의 판단대로 행이 묶이는지가 핵심입니다."""

from __future__ import annotations

import json

from tests_helpers import batch_output_line, write_batch_results, write_parquet

from data_pipeline.batch.raw_source import discover_datasets
from data_pipeline.config import Settings
from data_pipeline.schemas import DatasetProfile, ExtractedRecipe, ExtractedStorageItem
from data_pipeline.stages import STAGE_EXTRACT, extract

KOREAN_INGREDIENTS = [
    {"recipe_name": "흰밥", "원재료": "멥쌀", "식재료 보관 상태": "서늘한 곳"},
    {"recipe_name": "흰밥", "원재료": "물", "식재료 보관 상태": "정수"},
    {"recipe_name": "김밥", "원재료": "김", "식재료 보관 상태": "건조"},
]
KOREAN_STEPS = [
    {"recipe_name": "흰밥", "과정": "쌀 씻기", "내용": "쌀을 씻는다"},
    {"recipe_name": "김밥", "과정": "말기", "내용": "김에 말아준다"},
]
STORAGE_ROWS = [
    {"product_id": "fk_1", "name_en": "Butter", "storage": "pantry", "value_min": None},
    {"product_id": "fk_1", "name_en": "Butter", "storage": "dop_refrigerate", "value_min": 1.0},
    {"product_id": "fk_2", "name_en": "Milk", "storage": "refrigerate", "value_min": 7.0},
]


def make_profile(dataset: str, **overrides: object) -> DatasetProfile:
    """DatasetProfile 하나를 만듭니다."""
    payload: dict[str, object] = {
        "dataset": dataset,
        "summary": "요약",
        "language": "ko",
        "target_tables": ["recipe", "recipe_ingredient"],
        "rows_per_entity": "many",
        "group_by_columns": ["recipe_name"],
        "entity_key_columns": ["recipe_name"],
        "content_columns": ["원재료"],
        "companion_datasets": [],
        "loadable": True,
        "skip_reason": None,
        "column_meanings": [{"column": "원재료", "meaning": "재료명", "target_field": None}],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return DatasetProfile.model_validate(payload)


def test_many_rows_group_into_one_entity(tmp_settings: Settings) -> None:
    """한 레시피가 여러 행에 흩어져 있으면 그룹 키로 묶여야 합니다."""
    write_parquet(tmp_settings.raw_dir / "korean_recipe_ingredients.parquet", KOREAN_INGREDIENTS)
    dataset = discover_datasets(tmp_settings.raw_dir)[0]

    entities = list(extract.iter_entities(dataset, make_profile(dataset.name)))
    assert [entity.key for entity in entities] == ["흰밥", "김밥"]
    assert len(entities[0].rows) == 2  # 멥쌀 + 물


def test_one_row_per_entity_stays_separate(tmp_settings: Settings) -> None:
    """rows_per_entity 가 one 이면 같은 키라도 행마다 따로 갑니다."""
    write_parquet(tmp_settings.raw_dir / "storage_guide.parquet", STORAGE_ROWS)
    dataset = discover_datasets(tmp_settings.raw_dir)[0]
    profile = make_profile(
        dataset.name,
        target_tables=["storage_guideline"],
        rows_per_entity="one",
        group_by_columns=[],
        entity_key_columns=["product_id"],
    )

    entities = list(extract.iter_entities(dataset, profile))
    assert len(entities) == 3


def test_companion_rows_are_joined(tmp_settings: Settings) -> None:
    """재료 표와 조리 단계 표를 같이 보면 추출 품질이 올라갑니다."""
    write_parquet(tmp_settings.raw_dir / "korean_recipe_ingredients.parquet", KOREAN_INGREDIENTS)
    write_parquet(tmp_settings.raw_dir / "korean_recipe_steps.parquet", KOREAN_STEPS)
    datasets = {item.name: item for item in discover_datasets(tmp_settings.raw_dir)}

    main_profile = make_profile("korean_recipe_ingredients", companion_datasets=["korean_recipe_steps"])
    companion_profile = make_profile("korean_recipe_steps")

    entities = list(
        extract.iter_entities(
            datasets["korean_recipe_ingredients"],
            main_profile,
            companions={"korean_recipe_steps": (datasets["korean_recipe_steps"], companion_profile)},
        )
    )
    assert entities[0].companions["korean_recipe_steps"][0]["내용"] == "쌀을 씻는다"


def test_limit_caps_entities(tmp_settings: Settings) -> None:
    """비용을 조절하며 실험할 수 있어야 합니다."""
    write_parquet(tmp_settings.raw_dir / "korean_recipe_ingredients.parquet", KOREAN_INGREDIENTS)
    dataset = discover_datasets(tmp_settings.raw_dir)[0]

    entities = list(extract.iter_entities(dataset, make_profile(dataset.name), limit=1))
    assert len(entities) == 1


def test_target_model_follows_profile() -> None:
    """어느 스키마로 뽑을지는 프로파일이 고른 테이블이 정합니다."""
    assert extract.target_model(make_profile("a")) is ExtractedRecipe
    assert extract.target_model(make_profile("b", target_tables=["storage_guideline"])) is ExtractedStorageItem
    assert extract.target_model(make_profile("c", target_tables=["none"])) is None


def test_build_skips_unloadable_datasets(tmp_settings: Settings) -> None:
    """loadable=false 인 데이터셋은 요청을 만들지 않습니다."""
    write_parquet(tmp_settings.raw_dir / "korean_recipe_ingredients.parquet", KOREAN_INGREDIENTS)
    write_parquet(tmp_settings.raw_dir / "foodkeeper_xls_version.parquet", [{"Data_Version_Number": "1"}])

    profiles = {
        "korean_recipe_ingredients": make_profile("korean_recipe_ingredients"),
        "foodkeeper_xls_version": make_profile(
            "foodkeeper_xls_version", loadable=False, target_tables=["none"], skip_reason="버전 이력"
        ),
    }
    _, counts = extract.build(job_name="x1", profiles=profiles, settings=tmp_settings)
    assert counts == {"korean_recipe_ingredients": 2}


def test_prompt_includes_column_meanings(tmp_settings: Settings) -> None:
    """파이썬이 컬럼명을 모르므로, 프로파일의 해석이 프롬프트로 전달돼야 합니다."""
    write_parquet(tmp_settings.raw_dir / "korean_recipe_ingredients.parquet", KOREAN_INGREDIENTS)
    paths, _ = extract.build(
        job_name="x1",
        profiles={"korean_recipe_ingredients": make_profile("korean_recipe_ingredients")},
        settings=tmp_settings,
    )
    system = json.loads(paths[0].read_text(encoding="utf-8").splitlines()[0])["body"]["messages"][0]["content"]

    assert "원재료: 재료명" in system
    assert "한국어" in system  # 언어 정규화 지시


def test_collect_dispatches_schema_per_request(tmp_settings: Settings) -> None:
    """한 배치에 레시피용과 보관기준용이 섞여도 각자 스키마로 검증돼야 합니다."""
    write_parquet(tmp_settings.raw_dir / "korean_recipe_ingredients.parquet", KOREAN_INGREDIENTS[:2])
    write_parquet(tmp_settings.raw_dir / "storage_guide.parquet", STORAGE_ROWS[:1])

    profiles = {
        "korean_recipe_ingredients": make_profile("korean_recipe_ingredients"),
        "storage_guide": make_profile(
            "storage_guide",
            target_tables=["storage_guideline"],
            rows_per_entity="one",
            group_by_columns=[],
            entity_key_columns=["product_id"],
        ),
    }
    extract.build(job_name="x1", profiles=profiles, settings=tmp_settings)
    key_map = json.loads(
        (tmp_settings.stage_dir(STAGE_EXTRACT) / "requests" / "x1_keys.json").read_text(encoding="utf-8")
    )
    recipe_id = next(k for k, v in key_map.items() if v["dataset"] == "korean_recipe_ingredients")
    storage_id = next(k for k, v in key_map.items() if v["dataset"] == "storage_guide")

    write_batch_results(
        tmp_settings.stage_dir(STAGE_EXTRACT) / "results",
        "x1",
        [
            batch_output_line(
                recipe_id,
                {
                    "source_recipe_id": "흰밥",
                    "name": "흰밥",
                    "name_original": None,
                    "description": None,
                    "cuisine_type": "한식",
                    "difficulty": "EASY",
                    "prep_time_min": None,
                    "cook_time_min": 30,
                    "servings": 2,
                    "cooking_method": "밥짓기",
                    "tags": ["한식"],
                    "nutrition": None,
                    "ingredients": [
                        {
                            "raw_text": "멥쌀",
                            "name": "멥쌀",
                            "name_original": None,
                            "normalized_name": "멥쌀",
                            "quantity": None,
                            "unit": None,
                            "is_required": True,
                            "is_raw_material": True,
                            "purpose": None,
                        }
                    ],
                    "image_url": None,
                    "steps": [
                        {"step_no": 1, "instruction": "쌀을 씻는다", "image_url": None},
                        {"step_no": 2, "instruction": "밥을 짓는다", "image_url": None},
                    ],
                    "confidence": 0.9,
                    "reason": "재료 목록에서 추출",
                },
            ),
            batch_output_line(
                storage_id,
                {
                    "source_item_id": "fk_1",
                    "source_food_name": "Butter",
                    "source_food_subtitle": None,
                    "food_name_ko": "버터",
                    "normalized_name": "버터",
                    "rules": [
                        {
                            "source_slot": "pantry",
                            "duration_min": None,
                            "duration_max": None,
                            "duration_unit": None,
                            "duration_text": "실온 1-2일",
                            "storage_tips": "실온에 오래 두지 않는다",
                        }
                    ],
                    "confidence": 0.8,
                    "reason": "pantry 행에서 추출",
                },
            ),
        ],
    )

    counts, failures = extract.collect("x1", profiles=profiles, settings=tmp_settings)
    assert not failures
    assert counts["korean_recipe_ingredients"] == 1
    assert counts["storage_guide"] == 1

    records = extract.load_records("storage_guide", tmp_settings)
    assert records[0]["food_name_ko"] == "버터"
    assert records[0]["_entity_key"] == "fk_1"


def test_recipe_prompt_states_normalization_rules(tmp_settings: Settings) -> None:
    """소규모 실행에서 실제로 어긋났던 세 가지가 프롬프트에 명시돼 있어야 합니다.

    - 단위가 'tablespoons' 와 '큰술' 로 섞여 나왔습니다.
    - description 에 출처 URL 만 들어간 레시피가 5건 중 3건이었습니다.
    - 조리 단계 instruction 이 '1. 1. 믹싱볼에...' 처럼 번호가 두 번 붙었습니다.
    """
    system = extract.RECIPE_SYSTEM_TEMPLATE

    assert "단위(unit)도 한국어로 통일한다" in system
    assert "description 에 URL 이나 출처 표기를 넣지 않는다" in system
    assert "원문의 번호 접두사를 뗀다" in system


def test_requests_split_by_token_budget(tmp_settings: Settings) -> None:
    """대기 토큰 한도 때문에 파일 하나가 예산을 넘으면 안 됩니다.

    실제로 3.81M 토큰짜리 파일 하나를 넣었다가 배치가 64초 만에
    token_limit_exceeded 로 죽었습니다.
    """
    from data_pipeline.batch.client import BatchRunner, estimate_tokens
    from data_pipeline.stages import STAGE_EXTRACT

    tmp_settings.batch_max_tokens = 5_000
    runner = BatchRunner(STAGE_EXTRACT, tmp_settings)
    requests = [{"custom_id": f"r-{i:03d}", "body": {"messages": [{"content": "가" * 5_000}]}} for i in range(6)]
    paths = runner.write_requests(requests, job_name="x9")

    assert len(paths) > 1, "예산을 넘겼는데 파일이 하나뿐입니다."
    for path in paths:
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert sum(estimate_tokens(line) for line in lines) <= tmp_settings.batch_max_tokens


def test_submit_skips_parts_already_submitted(tmp_settings: Settings) -> None:
    """파트를 나눠 순차 제출하려면 이미 넣은 것을 다시 넣지 않아야 합니다."""
    from data_pipeline.batch.client import BatchJob, BatchRunner
    from data_pipeline.stages import STAGE_EXTRACT

    runner = BatchRunner(STAGE_EXTRACT, tmp_settings)
    runner.requests_dir.mkdir(parents=True, exist_ok=True)
    for part in (1, 2, 3):
        (runner.requests_dir / f"x9_part{part:03d}_input.jsonl").write_text("{}\n", encoding="utf-8")

    assert len(runner.pending_parts("x9")) == 3
    runner.save_manifest(
        "x9",
        [BatchJob("x9_part001_input.jsonl", "file-1", "batch_1", "completed", "2026-09-09T00:00:00+00:00")],
    )
    pending = runner.pending_parts("x9")
    assert [path.name for path in pending] == ["x9_part002_input.jsonl", "x9_part003_input.jsonl"]
