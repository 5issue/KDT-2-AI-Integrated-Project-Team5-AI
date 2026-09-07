"""Batch API 입력 JSONL 을 업로드하고 배치 잡을 생성합니다."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI

from data_pipeline.config import Settings, get_settings


@dataclass(slots=True)
class BatchJob:
    """배치 한 파트의 추적 정보. 매니페스트 파일로 저장됩니다."""

    input_file: str
    input_file_id: str
    batch_id: str
    status: str
    submitted_at: str
    output_file_id: str | None = None
    error_file_id: str | None = None


def manifest_path(job_name: str, settings: Settings | None = None) -> Path:
    """잡 이름에 대응하는 매니페스트 경로."""
    settings = settings or get_settings()
    return settings.batch_dir / f"{job_name}.manifest.json"


def save_manifest(job_name: str, jobs: list[BatchJob], settings: Settings | None = None) -> Path:
    """매니페스트를 저장합니다."""
    settings = settings or get_settings()
    settings.batch_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_path(job_name, settings)
    payload = {"job_name": job_name, "jobs": [asdict(job) for job in jobs]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_manifest(job_name: str, settings: Settings | None = None) -> list[BatchJob]:
    """매니페스트를 읽습니다."""
    path = manifest_path(job_name, settings)
    if not path.exists():
        raise FileNotFoundError(f"매니페스트가 없습니다: {path.name}. 먼저 submit 을 실행하세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [BatchJob(**item) for item in payload["jobs"]]


def create_client(settings: Settings | None = None) -> OpenAI:
    """설정의 키로 OpenAI 클라이언트를 만듭니다."""
    settings = settings or get_settings()
    return OpenAI(api_key=settings.require_openai_api_key())


def submit_batch_files(
    input_files: list[Path],
    *,
    job_name: str,
    settings: Settings | None = None,
    client: OpenAI | None = None,
) -> list[BatchJob]:
    """입력 파일들을 업로드하고 배치를 생성한 뒤 매니페스트를 남깁니다."""
    settings = settings or get_settings()
    if settings.dry_run:
        raise RuntimeError("DRY_RUN=true 인 상태에서는 배치를 제출하지 않습니다.")

    client = client or create_client(settings)
    jobs: list[BatchJob] = []
    for path in input_files:
        with path.open("rb") as handle:
            uploaded = client.files.create(file=handle, purpose="batch")
        batch = client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={"job_name": job_name, "input_file": path.name},
        )
        jobs.append(
            BatchJob(
                input_file=path.name,
                input_file_id=uploaded.id,
                batch_id=batch.id,
                status=batch.status,
                submitted_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
    save_manifest(job_name, jobs, settings)
    return jobs
