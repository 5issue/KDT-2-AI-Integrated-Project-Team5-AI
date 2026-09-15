"""raw 원문 읽기 테스트. 컬럼 구성을 가정하지 않는지가 핵심입니다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests_helpers import write_jsonl, write_parquet

from data_pipeline.batch.raw_source import (
    discover_datasets,
    iter_records,
    render_dataset_brief,
    sample_rows,
)

RECIPE_ROWS = [
    {"recipe_name": "흰밥", "원재료": "멥쌀", "식재료 보관 상태": "서늘한 곳"},
    {"recipe_name": "흰밥", "원재료": "물", "식재료 보관 상태": "정수"},
]
STORAGE_ROWS = [
    {"product_id": "fk_1", "name_en": "Butter", "storage": "pantry", "tips_en": "실온 1-2일"},
]


def test_reads_arbitrary_columns(tmp_path: Path) -> None:
    """source_id/text 같은 고정 컬럼을 요구하지 않아야 합니다."""
    write_parquet(tmp_path / "korean_recipe_ingredients.parquet", RECIPE_ROWS)

    dataset = discover_datasets(tmp_path)[0]
    assert set(dataset.columns) == {"recipe_name", "원재료", "식재료 보관 상태"}

    records = list(iter_records(dataset))
    assert [record.payload["원재료"] for record in records] == ["멥쌀", "물"]
    assert records[0].provenance == "korean_recipe_ingredients#0"


def test_same_columns_different_types_stay_separate(tmp_path: Path) -> None:
    """컬럼명은 같고 타입만 다른 사본이 하나로 합쳐지면 안 됩니다.

    실제 raw 에 foodkeeper_product(수치형)와 foodkeeper_xls_product(문자열)가 그렇습니다.
    합쳐지면 같은 내용이 두 배로 들어갑니다.
    """
    write_parquet(tmp_path / "numeric.parquet", [{"ID": 1, "Name": "Butter"}])
    write_parquet(tmp_path / "text.parquet", [{"ID": "1", "Name": "Butter"}])

    datasets = discover_datasets(tmp_path)
    assert {dataset.name for dataset in datasets} == {"numeric", "text"}
    assert all(dataset.row_count == 1 for dataset in datasets)


def test_same_schema_files_are_merged(tmp_path: Path) -> None:
    """날짜 파티션처럼 스키마가 완전히 같은 파일들은 한 데이터셋으로 묶입니다."""
    write_parquet(tmp_path / "dt=2026-09-07" / "part-0.parquet", RECIPE_ROWS[:1])
    write_parquet(tmp_path / "dt=2026-09-08" / "part-0.parquet", RECIPE_ROWS[1:])

    datasets = discover_datasets(tmp_path)
    assert len(datasets) == 1
    assert datasets[0].row_count == 2


def test_marker_files_are_ignored(tmp_path: Path) -> None:
    """_SUCCESS, .crc 때문에 실패하지 않아야 합니다."""
    write_parquet(tmp_path / "part-0.parquet", STORAGE_ROWS)
    (tmp_path / "_SUCCESS").write_text("", encoding="utf-8")
    (tmp_path / ".part-0.parquet.crc").write_bytes(b"\x00")

    assert len(discover_datasets(tmp_path)) == 1


def test_jsonl_schema_is_inferred(tmp_path: Path) -> None:
    """JSONL 은 스키마가 없어 앞쪽 줄의 키로 추론합니다."""
    write_jsonl(tmp_path / "crawled.jsonl", [{"title": "김치찌개", "body": "..."}])

    dataset = discover_datasets(tmp_path)[0]
    assert dataset.fmt == "jsonl"
    assert set(dataset.columns) == {"title", "body"}


def test_brief_contains_schema_and_samples(tmp_path: Path) -> None:
    """1단계 프롬프트 재료에 컬럼과 샘플이 모두 들어가야 합니다."""
    write_parquet(tmp_path / "storage_guide.parquet", STORAGE_ROWS)

    brief = render_dataset_brief(discover_datasets(tmp_path)[0], limit=1)
    assert "storage_guide" in brief
    assert "name_en" in brief
    assert "Butter" in brief


def test_long_values_are_shortened(tmp_path: Path) -> None:
    """레시피 본문이 통째로 프롬프트에 들어가면 토큰만 먹습니다."""
    write_parquet(tmp_path / "big.parquet", [{"body": "가" * 2000}])

    row = sample_rows(discover_datasets(tmp_path)[0], limit=1)[0]
    assert len(row["body"]) < 500
    assert row["body"].endswith("…(생략)")


def test_missing_path_is_rejected(tmp_path: Path) -> None:
    """경로 오타를 바로 알려줍니다."""
    with pytest.raises(FileNotFoundError, match="경로가 없습니다"):
        discover_datasets(tmp_path / "nope")


def test_empty_directory_is_rejected(tmp_path: Path) -> None:
    """빈 디렉터리로 파이프라인을 돌려 버리지 않게 막습니다."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="읽을 raw 파일"):
        discover_datasets(tmp_path / "empty")


def test_reads_public_data_json_with_records_wrapper(tmp_path: Path) -> None:
    """공공데이터 표준 JSON 은 {"fields": [...], "records": [...]} 형태입니다.

    이 형태를 못 읽어서 넣어 둔 파일 3개가 경고 없이 무시되고 있었습니다.
    """
    payload = {
        "fields": [{"id": "식품코드"}, {"id": "식품명"}],
        "records": [
            {"식품코드": "R112-1", "식품명": "국수"},
            {"식품코드": "R112-2", "식품명": "소면"},
        ],
    }
    (tmp_path / "nutrition.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    dataset = discover_datasets(tmp_path)[0]
    assert dataset.fmt == "json"
    assert dataset.row_count == 2
    assert set(dataset.columns) == {"식품코드", "식품명"}
    assert [r.payload["식품명"] for r in iter_records(dataset)] == ["국수", "소면"]


def test_reads_plain_json_array(tmp_path: Path) -> None:
    """최상위가 배열인 평범한 JSON 도 받습니다."""
    (tmp_path / "plain.json").write_text(json.dumps([{"a": 1}, {"a": 2}], ensure_ascii=False), encoding="utf-8")
    dataset = discover_datasets(tmp_path)[0]
    assert dataset.row_count == 2


def test_json_without_record_array_fails_loudly(tmp_path: Path) -> None:
    """레코드를 못 찾으면 조용히 넘어가지 않고 어떤 키가 있는지 알려 줍니다."""
    (tmp_path / "bad.json").write_text(json.dumps({"meta": {"x": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="레코드 배열을 찾지 못했습니다"):
        discover_datasets(tmp_path)


def test_reads_csv_with_bom(tmp_path: Path) -> None:
    """공공데이터 CSV 는 BOM 이 붙어 오는 일이 잦습니다. 붙으면 첫 컬럼명이 깨집니다."""
    (tmp_path / "food.csv").write_text("﻿식품코드,식품명\nR1,국수\nR2,소면\n", encoding="utf-8")
    dataset = discover_datasets(tmp_path)[0]
    assert dataset.fmt == "csv"
    assert "식품코드" in dataset.columns, "BOM 때문에 첫 컬럼명이 깨졌습니다"
    assert dataset.row_count == 2
