"""3단계 매칭 테스트. 임계값 아래는 채택하지 않는지가 핵심입니다."""

from __future__ import annotations

import json

import pytest
from tests_helpers import batch_output_line, write_batch_results, write_jsonl

from data_pipeline.config import Settings
from data_pipeline.stages import STAGE_RESOLVE, resolve

MASTER = [
    {"ingredient_id": 41, "name": "멥쌀", "normalized_name": "멥쌀", "aliases": [], "parent_ingredient_id": None},
    {
        "ingredient_id": 156,
        "name": "마늘",
        "normalized_name": "마늘",
        "aliases": ["깐마늘"],
        "parent_ingredient_id": None,
    },
]

RECIPE_RECORD = {
    "_dataset": "korean_recipe_ingredients",
    "_entity_key": "흰밥",
    "source_recipe_id": "흰밥",
    "name": "흰밥",
    "ingredients": [
        {"raw_text": "멥쌀 2컵", "name": "멥쌀", "normalized_name": "멥쌀"},
        {"raw_text": "다진 마늘", "name": "다진 마늘", "normalized_name": "마늘"},
        {"raw_text": "두부 반 모", "name": "두부", "normalized_name": "두부"},
    ],
}
STORAGE_RECORD = {
    "_dataset": "storage_guide",
    "_entity_key": "fk_1",
    "source_item_id": "fk_1",
    "source_food_name": "Butter",
    "food_name_ko": "버터",
    "normalized_name": "버터",
    "rules": [{"source_slot": "pantry", "duration_text": "실온 1-2일"}],
}


def seed_records(settings: Settings) -> None:
    """2단계 산출물 두 종류를 만들어 둡니다."""
    records = settings.artifacts_dir / "records"
    write_jsonl(records / "korean_recipe_ingredients.jsonl", [RECIPE_RECORD])
    write_jsonl(records / "storage_guide.jsonl", [STORAGE_RECORD])


def test_collects_names_from_both_record_kinds(tmp_settings: Settings) -> None:
    """레시피 재료와 보관기준 품목 모두 매칭 대상입니다."""
    seed_records(tmp_settings)
    names = resolve.collect_names(tmp_settings.artifacts_dir / "records")

    assert {item.normalized_name for item in names} == {"멥쌀", "마늘", "두부", "버터"}


def test_names_are_lowercased_and_deduplicated(tmp_settings: Settings) -> None:
    """같은 재료가 여러 번 나오면 한 번만 요청하고 등장 횟수를 셉니다."""
    records = tmp_settings.artifacts_dir / "records"
    write_jsonl(
        records / "a.jsonl",
        [
            {**RECIPE_RECORD, "ingredients": [{"raw_text": "A", "name": "마늘", "normalized_name": " 마늘 "}]},
            {**RECIPE_RECORD, "ingredients": [{"raw_text": "B", "name": "마늘", "normalized_name": "마늘"}]},
        ],
    )
    names = resolve.collect_names(records)

    assert len(names) == 1
    assert names[0].normalized_name == "마늘"
    assert names[0].occurrence == 2


def test_requests_are_chunked(tmp_settings: Settings) -> None:
    """마스터 목록 토큰을 여러 이름이 나눠 쓰도록 묶어 보냅니다."""
    settings = tmp_settings.model_copy(update={"match_chunk_size": 2})
    names = [resolve.NameRequest(f"재료{i}", f"재료{i}", f"재료{i} 1개", 1) for i in range(5)]

    requests, key_map = resolve.build_requests(names, MASTER, settings=settings)
    assert len(requests) == 3
    assert sum(len(chunk) for chunk in key_map.values()) == 5
    assert requests[0]["body"]["response_format"]["json_schema"]["name"] == "ingredient_match_batch"


def test_master_list_is_in_the_prompt(tmp_settings: Settings) -> None:
    """후보를 프롬프트에 넣어야 LLM 이 실제 마스터에서 고를 수 있습니다."""
    names = [resolve.NameRequest("버터", "버터", "Butter", 1)]
    requests, _ = resolve.build_requests(names, MASTER, settings=tmp_settings)
    system = requests[0]["body"]["messages"][0]["content"]

    assert "156|마늘|마늘 | 별칭 깐마늘" in system
    assert "가공식품은 없을 수 있다" in system  # 억지 매칭 방지 지시


def test_low_confidence_match_is_not_adopted(tmp_settings: Settings) -> None:
    """임계값 미만이면 마스터를 오염시키느니 미매칭으로 남깁니다."""
    settings = tmp_settings.model_copy(update={"match_min_confidence": 0.6})
    runner_dir = settings.stage_dir(STAGE_RESOLVE)
    (runner_dir / "requests").mkdir(parents=True, exist_ok=True)
    (runner_dir / "requests" / "r1_keys.json").write_text(
        json.dumps({"match-0000": ["두부", "버터"]}, ensure_ascii=False), encoding="utf-8"
    )
    write_batch_results(
        runner_dir / "results",
        "r1",
        [
            batch_output_line(
                "match-0000",
                {
                    "matches": [
                        {
                            "source_name": "두부",
                            "ingredient_id": 41,
                            "matched_name": "멥쌀",
                            "confidence": 0.2,
                            "reason": "확신 없음",
                        },
                        {
                            "source_name": "버터",
                            "ingredient_id": 156,
                            "matched_name": "마늘",
                            "confidence": 0.95,
                            "reason": "동일 재료",
                        },
                    ]
                },
            )
        ],
    )

    report = resolve.ResolveReport(
        total_names=2,
        unmatched=[
            {"normalized_name": "두부", "occurrence": 3, "confidence": 0.0},
            {"normalized_name": "버터", "occurrence": 1, "confidence": 0.0},
        ],
    )
    result = resolve.collect("r1", report=report, settings=settings)

    assert [item.normalized_name for item in result.llm] == ["버터"]
    assert [item["normalized_name"] for item in result.unmatched] == ["두부"]
    assert result.unmatched[0]["occurrence"] == 3  # 검토 우선순위를 위해 횟수를 유지


def test_missing_response_becomes_unmatched(tmp_settings: Settings) -> None:
    """요청했는데 응답에 빠진 이름이 조용히 사라지면 안 됩니다."""
    runner_dir = tmp_settings.stage_dir(STAGE_RESOLVE)
    (runner_dir / "requests").mkdir(parents=True, exist_ok=True)
    (runner_dir / "requests" / "r1_keys.json").write_text(
        json.dumps({"match-0000": ["두부", "밥"]}, ensure_ascii=False), encoding="utf-8"
    )
    write_batch_results(
        runner_dir / "results",
        "r1",
        [
            batch_output_line(
                "match-0000",
                {
                    "matches": [
                        {
                            "source_name": "두부",
                            "ingredient_id": None,
                            "matched_name": None,
                            "confidence": 0.1,
                            "reason": "마스터에 없음",
                        }
                    ]
                },
            )
        ],
    )

    report = resolve.ResolveReport(total_names=2, unmatched=[{"normalized_name": "밥", "occurrence": 1}])
    result = resolve.collect("r1", report=report, settings=tmp_settings)

    assert {item["normalized_name"] for item in result.unmatched} == {"두부", "밥"}
    assert any(item["reason"] == "응답 누락" for item in result.unmatched)


def test_report_round_trip(tmp_settings: Settings) -> None:
    """정확 일치 결과를 저장했다가 collect 가 이어받을 수 있어야 합니다."""
    report = resolve.ResolveReport(
        total_names=2,
        exact=[resolve.MatchResult("마늘", 156, "마늘", "exact", 1.0)],
        unmatched=[{"normalized_name": "두부", "occurrence": 1}],
    )
    resolve.save_report(report, tmp_settings)

    loaded = resolve.load_report(tmp_settings)
    assert [item.normalized_name for item in loaded.exact] == ["마늘"]
    assert loaded.unmatched[0]["normalized_name"] == "두부"
    assert resolve.load_matches(tmp_settings) == {"마늘": 156}


def test_render_shows_match_rate(tmp_settings: Settings) -> None:
    """매칭률과 미매칭 목록이 리포트에 나와야 검토할 수 있습니다."""
    report = resolve.ResolveReport(
        total_names=4,
        exact=[resolve.MatchResult("마늘", 156, "마늘", "exact", 1.0)],
        llm=[resolve.MatchResult("버터", 7, "버터", "llm", 0.9)],
        unmatched=[{"normalized_name": "두부", "occurrence": 5, "confidence": 0.2, "reason": "마스터에 없음"}],
    )
    rendered = report.render()

    assert "정확 일치        : 1종" in rendered
    assert "LLM 매칭         : 1종" in rendered
    assert "50.0%" in rendered
    assert "두부" in rendered


@pytest.mark.db
async def test_exact_match_against_real_master(tmp_settings: Settings) -> None:
    """실제 마스터에서 정확 일치가 동작하는지 확인합니다."""
    from data_pipeline.config import get_settings

    names = [
        resolve.NameRequest("마늘", "마늘", "다진 마늘", 1),
        resolve.NameRequest("존재하지않는재료xyz", "없음", "없음", 1),
    ]
    matched, remaining = await resolve.exact_match(names, get_settings())

    assert [item.normalized_name for item in matched] == ["마늘"]
    assert [item.normalized_name for item in remaining] == ["존재하지않는재료xyz"]
