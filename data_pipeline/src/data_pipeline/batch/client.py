"""OpenAI Batch API 실행기. 3단계가 같은 코드를 씁니다.

단계마다 하는 판단은 다르지만 흐름은 똑같습니다.
요청 JSONL 쓰기 -> 업로드/제출 -> 폴링 -> 결과 내려받기 -> 검증.
그래서 단계별 디렉터리만 다르게 주고 이 클래스를 공유합니다.

Batch API 는 최대 24시간이 걸릴 수 있어 제출과 수거를 별도 명령으로 나눠 두었습니다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from data_pipeline.config import Settings, get_settings
from data_pipeline.schemas import strict_json_schema

CHAT_COMPLETIONS_URL = "/v1/chat/completions"
TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})
# 종료됐지만 결과가 없는 상태. 더 기다려도 달라지지 않으므로 폴링을 멈춰야 합니다.
DEAD_STATUSES = frozenset({"failed", "expired", "cancelled"})

# OpenAI 제한: 입력 파일 하나에 50,000줄 / 200MB
HARD_MAX_REQUESTS = 50_000

# 토큰 추정용 문자수 나눗값. 한국어와 영어가 섞여 있어 정확하지는 않습니다.
# tiktoken 을 붙이면 정확해지지만 의존성이 늘어서, **적게 나눠 넘치는 쪽보다
# 많이 나눠 안전한 쪽**으로 보수적인 값을 씁니다. 넘치면 배치가 통째로 죽습니다.
CHARS_PER_TOKEN = 2.5


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
    error: str | None = None


@dataclass(slots=True)
class BatchFailure:
    """결과 한 줄이 실패했을 때의 기록."""

    custom_id: str
    reason: str
    detail: str | None = None


@dataclass(slots=True)
class BatchOutcome:
    """결과 파일 전체를 훑은 뒤의 집계."""

    records: list[tuple[str, BaseModel]] = field(default_factory=list)
    failures: list[BatchFailure] = field(default_factory=list)

    @property
    def total(self) -> int:
        """처리한 줄 수."""
        return len(self.records) + len(self.failures)

    def summary(self) -> str:
        """한 줄 요약."""
        return f"성공 {len(self.records)}건 / 실패 {len(self.failures)}건 (총 {self.total}건)"


def estimate_tokens(request: dict[str, Any]) -> int:
    """요청 한 줄의 입력 토큰 어림값. 파일을 나눌 기준으로만 씁니다."""
    body = request.get("body", {})
    chars = sum(len(str(message.get("content", ""))) for message in body.get("messages", []))
    return int(chars / CHARS_PER_TOKEN) + 1


def build_chat_request(
    custom_id: str,
    *,
    system: str,
    user: str,
    model: str,
    schema_name: str,
    schema_model: type[BaseModel],
    max_output_tokens: int = 8192,
) -> dict[str, Any]:
    """structured outputs strict 모드로 도는 Batch 요청 한 줄을 만듭니다."""
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": CHAT_COMPLETIONS_URL,
        "body": {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": strict_json_schema(schema_model),
                },
            },
            "max_completion_tokens": max_output_tokens,
        },
    }


class BatchRunner:
    """단계 하나의 배치 입출력을 담당합니다."""

    def __init__(self, stage: str, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self.stage = stage
        self.settings = settings or get_settings()
        self._client = client
        self.requests_dir = self.settings.stage_dir(stage) / "requests"
        self.results_dir = self.settings.stage_dir(stage) / "results"

    @property
    def client(self) -> OpenAI:
        """지연 생성. API 키가 없어도 파일 작업만 하는 경로는 동작합니다."""
        if self._client is None:
            self._client = OpenAI(api_key=self.settings.require_openai_api_key())
        return self._client

    # --- 요청 쓰기 ---------------------------------------------------------

    def write_requests(self, requests: Iterable[dict[str, Any]], *, job_name: str) -> list[Path]:
        """요청들을 JSONL 로 떨어뜨립니다. 줄 수 제한을 넘으면 파트로 나눕니다."""
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        limit = min(self.settings.batch_max_requests, HARD_MAX_REQUESTS)
        token_limit = self.settings.batch_max_tokens

        written: list[Path] = []
        seen: set[str] = set()
        part = 1
        count = 0
        used = 0
        handle = None
        try:
            for request in requests:
                custom_id = str(request["custom_id"])
                if custom_id in seen:
                    raise ValueError(
                        f"custom_id 가 중복입니다: {custom_id}. Batch API 는 파일 안에서 custom_id 가 유일해야 합니다."
                    )
                seen.add(custom_id)

                tokens = estimate_tokens(request)
                # 요청 하나가 상한을 넘으면 새 파트를 열어도 소용이 없습니다.
                # `count > 0` 조건 때문에 그런 요청은 그냥 담겨서, 상한을 두는 이유였던
                # token_limit_exceeded 를 그대로 다시 맞습니다. 여기서 멈춥니다.
                if tokens > token_limit:
                    raise ValueError(
                        f"{custom_id}: 요청 하나의 추정 토큰이 {tokens:,} 로 "
                        f"BATCH_MAX_TOKENS({token_limit:,}) 를 넘습니다. "
                        "EXTRACT_MAX_ENTITIES 를 줄이거나 상한을 올리세요."
                    )
                # 줄 수와 토큰 둘 다 본다. 토큰이 먼저 차는 쪽이 보통 레시피 데이터입니다.
                over_tokens = count > 0 and used + tokens > token_limit
                if handle is None or count >= limit or over_tokens:
                    if handle is not None:
                        handle.close()
                    path = self.requests_dir / f"{job_name}_part{part:03d}_input.jsonl"
                    handle = path.open("w", encoding="utf-8")
                    written.append(path)
                    part += 1
                    count = 0
                    used = 0
                handle.write(json.dumps(request, ensure_ascii=False) + "\n")
                count += 1
                used += tokens
        finally:
            if handle is not None:
                handle.close()

        if not written:
            raise ValueError(f"{self.stage}: 만들어진 요청이 없습니다.")
        return written

    # --- 제출 / 수거 -------------------------------------------------------

    def manifest_path(self, job_name: str) -> Path:
        """잡 이름에 대응하는 매니페스트 경로."""
        return self.results_dir / f"{job_name}.manifest.json"

    def save_manifest(self, job_name: str, jobs: Sequence[BatchJob]) -> Path:
        """매니페스트를 저장합니다."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.manifest_path(job_name)
        payload = {"stage": self.stage, "job_name": job_name, "jobs": [asdict(job) for job in jobs]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_manifest(self, job_name: str) -> list[BatchJob]:
        """매니페스트를 읽습니다."""
        path = self.manifest_path(job_name)
        if not path.exists():
            raise FileNotFoundError(f"{self.stage}: 매니페스트가 없습니다({path.name}). 먼저 submit 을 실행하세요.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [BatchJob(**item) for item in payload["jobs"]]

    def pending_parts(self, job_name: str) -> list[Path]:
        """아직 제출하지 않았거나, 제출했지만 죽은 파트.

        실패/만료/취소된 파트는 결과가 없으므로 다시 넣어야 합니다. 매니페스트에 있다는
        이유로 건너뛰면 그 파트가 조용히 빠진 채로 파이프라인이 끝납니다.
        """
        inputs = sorted(self.requests_dir.glob(f"{job_name}_part*_input.jsonl"))
        try:
            jobs = self.load_manifest(job_name)
        except FileNotFoundError:
            return inputs
        alive = {job.input_file for job in jobs if job.status not in DEAD_STATUSES}
        return [path for path in inputs if path.name not in alive]

    def submit(self, job_name: str, *, max_parts: int | None = None) -> list[BatchJob]:
        """아직 제출하지 않은 입력 파일을 업로드하고 배치를 생성합니다.

        `max_parts` 로 한 번에 몇 파트만 넣을 수 있습니다. OpenAI 의 대기 토큰 한도는
        조직 단위라, 전부 한꺼번에 넣으면 넘칩니다. 한 파트가 끝난 뒤 다음을 넣는 식으로
        나눠 제출하면 됩니다. 이미 제출한 파트는 매니페스트에 남아 다시 넣지 않습니다.
        """
        pending = self.pending_parts(job_name)
        if not pending:
            if sorted(self.requests_dir.glob(f"{job_name}_part*_input.jsonl")):
                return []
            raise FileNotFoundError(f"{self.stage}: {job_name} 의 입력 파일이 없습니다. 먼저 build 를 실행하세요.")
        if self.settings.dry_run:
            raise RuntimeError("DRY_RUN=true 인 상태에서는 배치를 제출하지 않습니다.")

        try:
            jobs: list[BatchJob] = self.load_manifest(job_name)
        except FileNotFoundError:
            jobs = []
        targets = pending[:max_parts] if max_parts else pending
        # 죽은 파트를 재제출하는 경우, 매니페스트의 옛 기록은 새 배치로 갈아끼웁니다.
        retry = {path.name for path in targets}
        jobs = [job for job in jobs if job.input_file not in retry]
        for path in targets:
            with path.open("rb") as handle:
                uploaded = self.client.files.create(file=handle, purpose="batch")
            batch = self.client.batches.create(
                input_file_id=uploaded.id,
                endpoint=CHAT_COMPLETIONS_URL,
                completion_window="24h",
                metadata={"stage": self.stage, "job_name": job_name, "input_file": path.name},
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
            # 파트마다 저장합니다. 루프가 끝난 뒤에만 저장하면, 중간 파트에서 실패했을 때
            # 이미 생성된 원격 배치가 매니페스트에 없는 채로 남습니다. 그 상태로 재시도하면
            # 같은 파트를 또 제출해 LLM 비용을 두 번 냅니다.
            self.save_manifest(job_name, jobs)
        return jobs

    def refresh(self, job_name: str) -> list[BatchJob]:
        """각 배치 상태를 최신으로 갱신해 다시 저장합니다."""
        jobs = self.load_manifest(job_name)
        for job in jobs:
            batch = self.client.batches.retrieve(job.batch_id)
            job.status = batch.status
            job.output_file_id = batch.output_file_id
            job.error_file_id = batch.error_file_id
            job.error = _first_error(batch)
        self.save_manifest(job_name, jobs)
        return jobs

    def wait(self, job_name: str, *, poll_seconds: int = 60, timeout_seconds: int = 24 * 60 * 60) -> list[BatchJob]:
        """모든 배치가 종료 상태가 될 때까지 폴링합니다."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            jobs = self.refresh(job_name)
            if all(job.status in TERMINAL_STATUSES for job in jobs):
                return jobs
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{self.stage}/{job_name}: {timeout_seconds}초 안에 배치가 끝나지 않았습니다.")
            time.sleep(poll_seconds)

    def download(self, job_name: str) -> list[Path]:
        """완료된 배치의 output/error JSONL 을 내려받습니다."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        for job in self.load_manifest(job_name):
            stem = job.input_file.removesuffix("_input.jsonl")
            for file_id, suffix in ((job.output_file_id, "output"), (job.error_file_id, "error")):
                if not file_id:
                    continue
                target = self.results_dir / f"{stem}_{suffix}.jsonl"
                target.write_bytes(self.client.files.content(file_id).read())
                downloaded.append(target)
        return downloaded

    # --- 결과 파싱 ---------------------------------------------------------

    def result_files(self, job_name: str) -> list[Path]:
        """이 잡의 output JSONL 목록."""
        return sorted(self.results_dir.glob(f"{job_name}_part*_output.jsonl"))

    def iter_contents(self, job_name: str) -> Iterator[tuple[str, str | None, BatchFailure | None]]:
        """결과를 (custom_id, 모델 출력, 실패) 로 한 줄씩 흘려 보냅니다.

        한 배치 안에서 요청마다 다른 스키마를 쓴 경우(2단계가 그렇습니다) 호출한 쪽이
        custom_id 로 스키마를 골라 검증할 수 있게 열어 둡니다.
        """
        files = self.result_files(job_name)
        if not files:
            raise FileNotFoundError(f"{self.stage}: {job_name} 의 결과 파일이 없습니다. collect 를 먼저 실행하세요.")
        for line in _iter_jsonl(files):
            custom_id = str(line.get("custom_id", "<unknown>"))
            content, failure = _extract_content(line)
            yield custom_id, content, failure

    def parse(self, job_name: str, schema_model: type[BaseModel]) -> BatchOutcome:
        """결과 JSONL 을 읽어 Pydantic 으로 검증합니다."""
        outcome = BatchOutcome()
        for custom_id, content, failure in self.iter_contents(job_name):
            if failure is not None:
                outcome.failures.append(failure)
                continue
            assert content is not None
            try:
                outcome.records.append((custom_id, schema_model.model_validate_json(content)))
            except ValidationError as exc:
                outcome.failures.append(
                    BatchFailure(custom_id=custom_id, reason="schema_validation", detail=str(exc)[:500])
                )
        return outcome


def _first_error(batch: Any) -> str | None:
    """배치 자체가 실패했을 때의 사유. 요청 단위 실패가 아니라 배치 전체 거절입니다.

    한도 초과(`token_limit_exceeded`)처럼 몇 초 만에 죽는 경우가 있어, 이 값이 없으면
    호출한 쪽이 "아직 도는 중" 과 구분하지 못합니다.
    """
    errors = getattr(batch, "errors", None)
    data = getattr(errors, "data", None) if errors is not None else None
    if not data:
        return None
    first = data[0]
    code = getattr(first, "code", None)
    message = getattr(first, "message", None)
    return f"{code}: {message}" if code else str(message)


def _iter_jsonl(files: Sequence[Path]) -> Iterator[dict[str, Any]]:
    """JSONL 여러 개를 한 줄씩."""
    for file in files:
        with file.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _extract_content(line: dict[str, Any]) -> tuple[str | None, BatchFailure | None]:
    """결과 한 줄에서 모델 출력 문자열만 꺼냅니다. 실패면 사유를 돌려줍니다."""
    custom_id = str(line.get("custom_id", "<unknown>"))

    if line.get("error"):
        return None, BatchFailure(custom_id=custom_id, reason="batch_error", detail=str(line["error"])[:500])

    response = line.get("response")
    if not isinstance(response, dict):
        return None, BatchFailure(custom_id=custom_id, reason="missing_response")
    if response.get("status_code") != 200:
        return None, BatchFailure(custom_id=custom_id, reason="http_error", detail=str(response.get("status_code")))

    body = response.get("body")
    if not isinstance(body, dict):
        return None, BatchFailure(custom_id=custom_id, reason="missing_body")

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, BatchFailure(custom_id=custom_id, reason="empty_choices")

    message = choices[0].get("message", {})
    if not isinstance(message, dict):
        return None, BatchFailure(custom_id=custom_id, reason="bad_message")
    if message.get("refusal"):
        return None, BatchFailure(custom_id=custom_id, reason="refusal", detail=str(message["refusal"])[:500])

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, BatchFailure(custom_id=custom_id, reason="empty_content")
    return content, None
