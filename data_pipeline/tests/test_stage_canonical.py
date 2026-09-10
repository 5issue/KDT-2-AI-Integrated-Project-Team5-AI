"""영문 canonical 클러스터링 테스트.

케이스는 전부 실제 2단계 산출물(1,178건)에서 나온 것들입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.stages import canonical


def write_records(records_dir: Path, rows: list[dict[str, object]]) -> None:
    """레시피 한 건에 재료 여러 개를 담아 산출물 형태로 씁니다."""
    records_dir.mkdir(parents=True, exist_ok=True)
    payload = {"source_recipe_id": "r1", "name": "테스트", "ingredients": rows}
    (records_dir / "recipes.jsonl").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def ing(original: str, korean: str) -> dict[str, object]:
    """재료 한 줄."""
    return {"raw_text": original, "name": korean, "name_original": original, "normalized_name": korean}


def test_clusters_group_by_english_original(tmp_path: Path) -> None:
    """같은 영문이 여러 한국어로 번역된 것을 한 덩어리로 묶습니다."""
    write_records(
        tmp_path,
        [ing("eggs", "계란"), ing("eggs", "달걀"), ing("eggs", "계란"), ing("butter", "버터")],
    )
    clusters = canonical.build_clusters(tmp_path)

    assert set(clusters.members) == {"eggs", "butter"}
    # 등장 횟수 내림차순이라 계란(2)이 앞
    assert clusters.variants("eggs") == ["계란", "달걀"]
    assert clusters.split_count() == 1


def test_propagate_uses_the_variant_that_matched_the_master(tmp_path: Path) -> None:
    """마스터에는 `달걀` 만 있습니다. `계란` 147건이 번역 운으로 빠지면 안 됩니다."""
    write_records(tmp_path, [ing("eggs", "계란"), ing("eggs", "달걀")])
    clusters = canonical.build_clusters(tmp_path)

    gained = canonical.propagate(clusters, {"달걀": 42})
    assert gained == {"계란": 42}


def test_propagate_leaves_already_matched_alone(tmp_path: Path) -> None:
    """이미 붙어 있는 키를 덮어쓰지 않습니다."""
    write_records(tmp_path, [ing("eggs", "계란"), ing("eggs", "달걀")])
    clusters = canonical.build_clusters(tmp_path)

    gained = canonical.propagate(clusters, {"달걀": 42, "계란": 7})
    assert gained == {}


def test_propagate_prefers_the_most_frequent_variant_on_conflict(tmp_path: Path) -> None:
    """한 클러스터에서 서로 다른 id 로 붙으면 많이 나온 쪽을 씁니다. 실행마다 흔들리면 안 됩니다."""
    write_records(
        tmp_path,
        [ing("sugar", "설탕"), ing("sugar", "설탕"), ing("sugar", "당류"), ing("sugar", "감미료")],
    )
    clusters = canonical.build_clusters(tmp_path)

    gained = canonical.propagate(clusters, {"설탕": 1, "당류": 2})
    assert gained == {"감미료": 1}  # 설탕(2회)이 당류(1회)를 이깁니다


def test_representatives_ask_once_per_cluster(tmp_path: Path) -> None:
    """`바닐라 추출물` 계열 8개를 8번 물어볼 이유가 없습니다."""
    write_records(
        tmp_path,
        [
            ing("vanilla extract", "바닐라추출물"),
            ing("vanilla extract", "바닐라추출물"),
            ing("vanilla extract", "바닐라"),
            ing("vanilla extract", "바닐라익스트랙"),
            ing("salt", "소금"),
        ],
    )
    clusters = canonical.build_clusters(tmp_path)
    unmatched = {"바닐라추출물", "바닐라", "바닐라익스트랙", "소금"}

    delegate = canonical.representatives(clusters, unmatched)
    heads = set(delegate.values())

    # 바닐라 계열 3종이 대표 하나로, 소금은 자기 자신이 대표
    assert delegate["바닐라"] == "바닐라추출물"
    assert delegate["바닐라익스트랙"] == "바닐라추출물"
    assert delegate["소금"] == "소금"
    assert len(heads) == 2


def test_representatives_skip_variants_already_matched(tmp_path: Path) -> None:
    """이미 붙은 변형은 대표 후보에서 빠집니다."""
    write_records(tmp_path, [ing("eggs", "계란"), ing("eggs", "달걀")])
    clusters = canonical.build_clusters(tmp_path)

    delegate = canonical.representatives(clusters, {"계란"})
    assert delegate == {"계란": "계란"}


def test_ingredients_without_original_are_not_clustered(tmp_path: Path) -> None:
    """한국어 원본 레시피는 name_original 이 없습니다. 이런 것은 자기 자신이 대표입니다."""
    write_records(
        tmp_path,
        [{"raw_text": "멥쌀", "name": "멥쌀", "name_original": None, "normalized_name": "멥쌀"}],
    )
    clusters = canonical.build_clusters(tmp_path)

    assert clusters.members == {}
    assert canonical.representatives(clusters, {"멥쌀"}) == {"멥쌀": "멥쌀"}
