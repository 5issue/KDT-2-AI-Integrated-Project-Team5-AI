"""raw 원문 읽기. parquet 과 JSONL 을 확장자로 판별해서 같은 형태로 내어 줍니다.

팀 기준 포맷은 **parquet** 입니다. JSONL 도 계속 읽을 수 있게 남겨 둔 이유는
`samples/` 가 JSONL 이고, 크롤링 직후 임시 확인에는 텍스트 포맷이 편하기 때문입니다.

parquet 은 `pyarrow.dataset` 으로 읽습니다. 파일 하나든, 여러 개든, 날짜별로 나뉜
파티션 디렉터리든 같은 코드로 처리되고, 배치 단위로 스트리밍해서 큰 파일에도
메모리가 터지지 않습니다.

필요한 컬럼은 JSONL 과 동일합니다.

| 컬럼 | 필수 | 설명 |
| --- | --- | --- |
| `source_id` | 예 | 원본 식별자. 배치 `custom_id` 와 `recipe.source_recipe_id` 가 됩니다 |
| `text` | 예 | 파싱할 원문 |
| `source_url` | 아니오 | 출처 링크 |

나머지 컬럼은 무시합니다. 크롤러가 붙인 부가 컬럼을 굳이 지우지 않아도 됩니다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

REQUIRED_COLUMNS: tuple[str, ...] = ("source_id", "text")
OPTIONAL_COLUMNS: tuple[str, ...] = ("source_url",)

PARQUET_SUFFIXES = frozenset({".parquet", ".pq"})
JSONL_SUFFIXES = frozenset({".jsonl", ".ndjson"})

# parquet 을 한 번에 몇 행씩 읽어 올지. 원문이 길어서 너무 크게 잡지 않습니다.
DEFAULT_BATCH_SIZE = 1024


@dataclass(slots=True)
class RawDocument:
    """파싱 대상 원문 한 건."""

    source_id: str
    text: str
    source_url: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, origin: str = "") -> RawDocument:
        """dict 한 건을 RawDocument 로 만듭니다. 값이 비면 조용히 넘기지 않고 실패시킵니다."""
        prefix = f"{origin}: " if origin else ""

        missing = [column for column in REQUIRED_COLUMNS if column not in payload]
        if missing:
            raise ValueError(f"{prefix}raw 문서에 필수 키가 없습니다: {missing}")

        blank = [column for column in REQUIRED_COLUMNS if not str(payload[column] or "").strip()]
        if blank:
            raise ValueError(f"{prefix}raw 문서의 {blank} 값이 비어 있습니다.")

        # 공백만 있는 URL 은 빈 문자열이 아니라 None 으로 둡니다.
        url = str(payload.get("source_url") or "").strip()
        return cls(
            source_id=str(payload["source_id"]).strip(),
            text=str(payload["text"]),
            source_url=url or None,
        )


@dataclass(slots=True)
class RawSource:
    """읽을 대상 파일 목록. 어떤 포맷이 몇 개인지 먼저 보여 주기 위한 것입니다."""

    parquet_files: tuple[Path, ...]
    jsonl_files: tuple[Path, ...]

    @property
    def is_empty(self) -> bool:
        """읽을 파일이 하나도 없는지."""
        return not self.parquet_files and not self.jsonl_files

    def describe(self) -> str:
        """사람이 읽을 요약."""
        parts = []
        if self.parquet_files:
            parts.append(f"parquet {len(self.parquet_files)}개")
        if self.jsonl_files:
            parts.append(f"JSONL {len(self.jsonl_files)}개")
        return " + ".join(parts) or "없음"


def discover_raw_source(path: Path) -> RawSource:
    """경로에서 읽을 수 있는 파일을 찾습니다. 디렉터리면 하위까지 훑습니다."""
    if not path.exists():
        raise FileNotFoundError(f"경로가 없습니다: {path}")

    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in PARQUET_SUFFIXES:
            return RawSource(parquet_files=(path,), jsonl_files=())
        if suffix in JSONL_SUFFIXES:
            return RawSource(parquet_files=(), jsonl_files=(path,))
        raise ValueError(
            f"지원하지 않는 확장자입니다: {path.name} "
            f"(parquet: {sorted(PARQUET_SUFFIXES)}, JSONL: {sorted(JSONL_SUFFIXES)})"
        )

    # `_` 나 `.` 로 시작하는 파일은 건너뜁니다. Spark 등이 남기는 _SUCCESS, .crc 가 여기 걸립니다.
    candidates = [item for item in sorted(path.rglob("*")) if item.is_file() and not item.name.startswith(("_", "."))]
    parquet_files = tuple(item for item in candidates if item.suffix.lower() in PARQUET_SUFFIXES)
    jsonl_files = tuple(item for item in candidates if item.suffix.lower() in JSONL_SUFFIXES)

    source = RawSource(parquet_files=parquet_files, jsonl_files=jsonl_files)
    if source.is_empty:
        raise FileNotFoundError(f"읽을 raw 파일을 찾지 못했습니다: {path} (parquet 또는 JSONL 이 있어야 합니다)")
    return source


def _assert_columns(available: Sequence[str], *, origin: str) -> list[str]:
    """필수 컬럼이 있는지 확인하고, 실제로 읽을 컬럼 목록을 돌려줍니다."""
    names = set(available)
    missing = [column for column in REQUIRED_COLUMNS if column not in names]
    if missing:
        raise ValueError(f"{origin}: 필수 컬럼이 없습니다: {missing}. 실제 컬럼: {sorted(names)}")
    return [column for column in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS) if column in names]


def iter_parquet_documents(
    files: Sequence[Path],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Iterator[RawDocument]:
    """parquet 파일들을 배치 단위로 스트리밍하며 읽습니다."""
    if not files:
        return

    # 파일 목록을 통째로 넘기면 pyarrow 가 스키마를 합쳐 줍니다.
    # 디렉터리 대신 목록을 넘기는 이유는, 같은 디렉터리에 JSONL 이 섞여 있어도
    # parquet 으로 읽으려 들지 않게 하기 위해서입니다.
    dataset = ds.dataset([str(file) for file in files], format="parquet")
    origin = files[0].name if len(files) == 1 else f"parquet {len(files)}개"
    columns = _assert_columns(dataset.schema.names, origin=origin)

    row_no = 0
    for batch in dataset.to_batches(columns=columns, batch_size=batch_size):
        for payload in batch.to_pylist():
            row_no += 1
            yield RawDocument.from_mapping(payload, origin=f"{origin} {row_no}행")


def iter_jsonl_documents(files: Sequence[Path]) -> Iterator[RawDocument]:
    """JSONL 파일들을 한 줄씩 읽습니다."""
    for file in files:
        with file.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{file.name}:{line_no} JSON 파싱 실패: {exc.msg}") from exc
                if not isinstance(payload, dict):
                    raise ValueError(f"{file.name}:{line_no} 한 줄은 JSON 객체여야 합니다.")
                yield RawDocument.from_mapping(payload, origin=f"{file.name}:{line_no}")


def iter_raw_documents(path: Path, *, batch_size: int = DEFAULT_BATCH_SIZE) -> Iterator[RawDocument]:
    """parquet / JSONL 파일 또는 그것들이 든 디렉터리에서 원문을 읽습니다.

    둘 다 있으면 parquet 을 먼저 읽습니다.
    """
    source = discover_raw_source(path)
    yield from iter_parquet_documents(source.parquet_files, batch_size=batch_size)
    yield from iter_jsonl_documents(source.jsonl_files)


def preview_raw_source(path: Path, *, limit: int = 3) -> str:
    """배치를 돌리기 전에 raw 데이터를 눈으로 확인하기 위한 요약.

    parquet 변환이 제대로 됐는지(컬럼명, 행 수, 본문이 안 잘렸는지) 보는 용도입니다.
    """
    source = discover_raw_source(path)
    lines = [f"경로     : {path}", f"파일     : {source.describe()}"]

    if source.parquet_files:
        dataset = ds.dataset([str(file) for file in source.parquet_files], format="parquet")
        lines.append(f"parquet 컬럼: {dataset.schema.names}")
        lines.append(f"parquet 행수: {dataset.count_rows()}")

    seen: set[str] = set()
    duplicates: list[str] = []
    total = 0
    samples: list[RawDocument] = []
    for doc in iter_raw_documents(path):
        total += 1
        if doc.source_id in seen:
            duplicates.append(doc.source_id)
        seen.add(doc.source_id)
        if len(samples) < limit:
            samples.append(doc)

    lines.append(f"총 문서  : {total}건 (고유 source_id {len(seen)}개)")
    if duplicates:
        preview = ", ".join(duplicates[:5])
        lines.append(f"중복 id  : {len(duplicates)}건 -> {preview}{' ...' if len(duplicates) > 5 else ''}")
        lines.append("           Batch API 는 파일 안에서 custom_id 가 유일해야 합니다. build 가 막습니다.")

    for doc in samples:
        body = doc.text.replace("\n", " ")[:80]
        lines.append(f"  [{doc.source_id}] {body}{'...' if len(doc.text) > 80 else ''}")
    return "\n".join(lines)
