"""배치 상태를 확인하고 완료된 결과/에러 파일을 내려받습니다."""

from __future__ import annotations

import time
from pathlib import Path

from openai import OpenAI

from data_pipeline.batch.submit import BatchJob, create_client, load_manifest, save_manifest
from data_pipeline.config import Settings, get_settings

TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})


def refresh_jobs(
    job_name: str,
    *,
    settings: Settings | None = None,
    client: OpenAI | None = None,
) -> list[BatchJob]:
    """매니페스트의 각 배치 상태를 최신으로 갱신해 다시 저장합니다."""
    settings = settings or get_settings()
    client = client or create_client(settings)

    jobs = load_manifest(job_name, settings)
    for job in jobs:
        batch = client.batches.retrieve(job.batch_id)
        job.status = batch.status
        job.output_file_id = batch.output_file_id
        job.error_file_id = batch.error_file_id
    save_manifest(job_name, jobs, settings)
    return jobs


def download_results(
    job_name: str,
    *,
    settings: Settings | None = None,
    client: OpenAI | None = None,
) -> list[Path]:
    """완료된 배치의 output/error JSONL 을 batch 디렉터리에 저장합니다."""
    settings = settings or get_settings()
    client = client or create_client(settings)

    downloaded: list[Path] = []
    for job in load_manifest(job_name, settings):
        stem = job.input_file.removesuffix("_input.jsonl")
        for file_id, suffix in ((job.output_file_id, "output"), (job.error_file_id, "error")):
            if not file_id:
                continue
            target = settings.batch_dir / f"{stem}_{suffix}.jsonl"
            target.write_bytes(client.files.content(file_id).read())
            downloaded.append(target)
    return downloaded


def wait_until_done(
    job_name: str,
    *,
    poll_seconds: int = 60,
    timeout_seconds: int = 24 * 60 * 60,
    settings: Settings | None = None,
    client: OpenAI | None = None,
) -> list[BatchJob]:
    """모든 배치가 종료 상태가 될 때까지 폴링합니다. 배치는 최대 24시간이 걸릴 수 있습니다."""
    settings = settings or get_settings()
    client = client or create_client(settings)

    deadline = time.monotonic() + timeout_seconds
    while True:
        jobs = refresh_jobs(job_name, settings=settings, client=client)
        if all(job.status in TERMINAL_STATUSES for job in jobs):
            return jobs
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{job_name}: {timeout_seconds}초 안에 배치가 끝나지 않았습니다.")
        time.sleep(poll_seconds)
