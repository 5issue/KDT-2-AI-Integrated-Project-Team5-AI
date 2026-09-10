"""테스트 보조 함수. conftest 는 pytest 가 특별 취급하므로 임포트용은 여기 둡니다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> Path:
    """딕셔너리 목록을 parquet 으로 씁니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    """딕셔너리 목록을 JSONL 로 씁니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def batch_output_line(custom_id: str, payload: dict[str, Any] | None, *, refusal: str | None = None) -> str:
    """Batch API output JSONL 한 줄을 흉내냅니다."""
    if refusal is not None:
        message: dict[str, Any] = {"role": "assistant", "content": None, "refusal": refusal}
    else:
        message = {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False), "refusal": None}
    return json.dumps(
        {
            "id": f"batch_req_{custom_id}",
            "custom_id": custom_id,
            "response": {
                "status_code": 200,
                "request_id": f"req_{custom_id}",
                "body": {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]},
            },
            "error": None,
        },
        ensure_ascii=False,
    )


def write_batch_results(results_dir: Path, job_name: str, lines: list[str]) -> Path:
    """가짜 결과 파일을 단계 디렉터리에 떨어뜨립니다."""
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{job_name}_part001_output.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
