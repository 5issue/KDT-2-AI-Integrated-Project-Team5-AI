"""raw 원문 읽기 테스트 (parquet / JSONL). DB·API 없이 돕니다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_pipeline.batch.raw_source import (
    RawDocument,
    discover_raw_source,
    iter_raw_documents,
    preview_raw_source,
)

ROWS: list[dict[str, Any]] = [
    {"source_id": "p-001", "text": "김치찌개\n재료: 김치 300g", "source_url": "https://example.com/1"},
    {"source_id": "p-002", "text": "된장찌개\n재료: 된장 2큰술", "source_url": None},
]


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> Path:
    """딕셔너리 목록을 parquet 으로 씁니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    """딕셔너리 목록을 JSONL 로 씁니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_reads_single_parquet_file(tmp_path: Path) -> None:
    """parquet 파일 하나를 그대로 읽습니다."""
    docs = list(iter_raw_documents(write_parquet(tmp_path / "recipes.parquet", ROWS)))

    assert [doc.source_id for doc in docs] == ["p-001", "p-002"]
    assert docs[0].source_url == "https://example.com/1"
    assert docs[1].source_url is None  # null 은 None 으로 옵니다


def test_reads_parquet_directory_including_partitions(tmp_path: Path) -> None:
    """날짜별로 나뉜 파티션 디렉터리도 하위까지 훑어 읽습니다."""
    write_parquet(tmp_path / "dt=2026-09-07" / "part-0.parquet", ROWS[:1])
    write_parquet(tmp_path / "dt=2026-09-08" / "part-0.parquet", ROWS[1:])

    docs = list(iter_raw_documents(tmp_path))
    assert {doc.source_id for doc in docs} == {"p-001", "p-002"}


def test_ignores_spark_marker_files(tmp_path: Path) -> None:
    """_SUCCESS, .crc 같은 마커 파일 때문에 실패하지 않아야 합니다."""
    write_parquet(tmp_path / "part-0.parquet", ROWS)
    (tmp_path / "_SUCCESS").write_text("", encoding="utf-8")
    (tmp_path / ".part-0.parquet.crc").write_bytes(b"\x00")

    source = discover_raw_source(tmp_path)
    assert len(source.parquet_files) == 1
    assert len(list(iter_raw_documents(tmp_path))) == 2


def test_extra_columns_are_ignored(tmp_path: Path) -> None:
    """크롤러가 붙인 부가 컬럼이 있어도 그대로 읽힙니다."""
    rows = [{**ROWS[0], "crawled_at": "2026-09-08", "score": 0.9}]
    docs = list(iter_raw_documents(write_parquet(tmp_path / "extra.parquet", rows)))

    assert len(docs) == 1
    assert docs[0].source_id == "p-001"


def test_missing_column_error_lists_actual_columns(tmp_path: Path) -> None:
    """컬럼명이 다르면 실제 컬럼 목록까지 알려줘야 고치기 쉽습니다."""
    rows = [{"id": "p-001", "body": "본문"}]
    path = write_parquet(tmp_path / "wrong.parquet", rows)

    with pytest.raises(ValueError, match="필수 컬럼이 없습니다") as exc:
        list(iter_raw_documents(path))
    message = str(exc.value)
    assert "source_id" in message
    assert "'body'" in message or "body" in message


def test_null_required_value_is_rejected(tmp_path: Path) -> None:
    """text 가 null 인 행이 조용히 빈 문서로 넘어가면 안 됩니다."""
    rows = [{"source_id": "p-001", "text": None, "source_url": None}]
    path = write_parquet(tmp_path / "null.parquet", rows)

    with pytest.raises(ValueError, match="비어 있습니다"):
        list(iter_raw_documents(path))


def test_batch_size_does_not_change_result(tmp_path: Path) -> None:
    """스트리밍 배치 크기를 바꿔도 읽히는 문서는 같아야 합니다."""
    rows = [{"source_id": f"p-{i:03d}", "text": f"본문 {i}", "source_url": None} for i in range(50)]
    path = write_parquet(tmp_path / "many.parquet", rows)

    small = [doc.source_id for doc in iter_raw_documents(path, batch_size=7)]
    large = [doc.source_id for doc in iter_raw_documents(path, batch_size=1000)]
    assert small == large == [row["source_id"] for row in rows]


def test_jsonl_still_works(tmp_path: Path) -> None:
    """기준 포맷은 parquet 이지만 JSONL 도 계속 읽혀야 합니다."""
    docs = list(iter_raw_documents(write_jsonl(tmp_path / "recipes.jsonl", ROWS)))
    assert [doc.source_id for doc in docs] == ["p-001", "p-002"]


def test_mixed_directory_reads_parquet_first(tmp_path: Path) -> None:
    """한 디렉터리에 둘 다 있으면 parquet 을 먼저 읽고 JSONL 도 빠뜨리지 않습니다."""
    write_parquet(tmp_path / "a.parquet", ROWS[:1])
    write_jsonl(tmp_path / "b.jsonl", ROWS[1:])

    assert [doc.source_id for doc in iter_raw_documents(tmp_path)] == ["p-001", "p-002"]


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    """csv 를 넘기면 조용히 무시하지 않고 알려줍니다."""
    path = tmp_path / "recipes.csv"
    path.write_text("source_id,text\n1,본문\n", encoding="utf-8")

    with pytest.raises(ValueError, match="지원하지 않는 확장자"):
        list(iter_raw_documents(path))


def test_empty_directory_is_rejected(tmp_path: Path) -> None:
    """빈 디렉터리로 배치를 만들어 버리지 않게 막습니다."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="읽을 raw 파일"):
        list(iter_raw_documents(tmp_path / "empty"))


def test_missing_path_is_rejected(tmp_path: Path) -> None:
    """경로 오타를 바로 알려줍니다."""
    with pytest.raises(FileNotFoundError, match="경로가 없습니다"):
        list(iter_raw_documents(tmp_path / "nope"))


def test_preview_reports_columns_rows_and_duplicates(tmp_path: Path) -> None:
    """inspect 가 컬럼/행수/중복 id 를 보여줘야 변환 결과를 검증할 수 있습니다."""
    rows = [*ROWS, {"source_id": "p-001", "text": "중복된 문서", "source_url": None}]
    write_parquet(tmp_path / "dup.parquet", rows)

    output = preview_raw_source(tmp_path, limit=2)
    assert "parquet 1개" in output
    assert "parquet 행수: 3" in output
    assert "총 문서  : 3건 (고유 source_id 2개)" in output
    assert "중복 id" in output and "p-001" in output


def test_raw_document_strips_whitespace() -> None:
    """source_id 앞뒤 공백은 다듬습니다. custom_id 와 FK 값이 되기 때문입니다."""
    doc = RawDocument.from_mapping({"source_id": "  p-001 ", "text": "본문", "source_url": " "})
    assert doc.source_id == "p-001"
    assert doc.source_url is None
