"""raw 원문 읽기. 컬럼 구성을 전혀 가정하지 않습니다.

raw 데이터는 CSV / PDF / 크롤링 텍스트 등 출처가 제각각인 것을 겨우 parquet(또는 JSONL)로
뽑아낸 것이라 스키마가 통일되어 있지 않습니다. 그래서 이 모듈은 컬럼명을 하나도 모른 채로
읽고, "어떤 컬럼이 무슨 뜻인지"는 1단계 프로파일링에서 LLM 이 판단합니다.

여기서 하는 일은 세 가지뿐입니다.
- 파일을 찾아 **스키마가 같은 것끼리 데이터셋으로 묶기** (날짜 파티션이 자연스럽게 합쳐집니다)
- 컬럼명/타입/행수/샘플행 요약 만들기 (1단계 프롬프트 재료)
- 행을 원본 dict 그대로 스트리밍하기
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

PARQUET_SUFFIXES = frozenset({".parquet", ".pq"})
JSONL_SUFFIXES = frozenset({".jsonl", ".ndjson"})
JSON_SUFFIXES = frozenset({".json"})
CSV_SUFFIXES = frozenset({".csv", ".tsv"})

# 공공데이터 JSON 이 레코드 배열을 담아 두는 키들. 먼저 찾은 것을 씁니다.
JSON_RECORD_KEYS = ("records", "data", "rows", "items", "result")

# parquet 을 한 번에 몇 행씩 읽어 올지.
DEFAULT_BATCH_SIZE = 1024

# 1단계 프롬프트에 넣을 샘플 행 수. 너무 많으면 토큰만 먹고 판단이 나아지지 않습니다.
DEFAULT_SAMPLE_ROWS = 5

# 샘플 행에서 값 하나를 이 길이까지만 보여 줍니다. 레시피 본문이 통째로 들어가는 것을 막습니다.
SAMPLE_VALUE_LIMIT = 400


@dataclass(slots=True)
class RawRecord:
    """원본 한 행. payload 는 파일에 있던 컬럼 그대로입니다."""

    dataset: str
    row_index: int
    payload: dict[str, Any]

    @property
    def provenance(self) -> str:
        """어느 데이터셋 몇 번째 행인지."""
        return f"{self.dataset}#{self.row_index}"


@dataclass(slots=True)
class RawDataset:
    """스키마가 같은 파일들의 묶음."""

    name: str
    files: tuple[Path, ...]
    fmt: str  # "parquet" | "jsonl"
    columns: dict[str, str] = field(default_factory=dict)
    row_count: int = 0

    def describe(self) -> str:
        """한 줄 요약."""
        return f"{self.name} ({self.fmt}, {len(self.files)}파일, {self.row_count}행, {len(self.columns)}컬럼)"


def _visible_files(path: Path) -> list[Path]:
    """읽을 후보 파일. `_` 나 `.` 로 시작하는 마커 파일(_SUCCESS, .crc)은 건너뜁니다."""
    if path.is_file():
        return [path]
    return [item for item in sorted(path.rglob("*")) if item.is_file() and not item.name.startswith(("_", "."))]


def _dataset_name(files: Sequence[Path], root: Path) -> str:
    """파일 묶음의 이름을 정합니다.

    파일이 하나면 그 이름, 여러 개면 공통 부모 디렉터리 이름을 씁니다
    (`dt=2026-09-08/part-0.parquet` 같은 파티션이 부모 이름 하나로 묶입니다).
    """
    if len(files) == 1:
        return files[0].stem
    stems = {file.stem for file in files}
    if len(stems) == 1:
        return stems.pop()
    parents = {file.parent for file in files}
    if len(parents) == 1:
        parent = parents.pop()
        if parent != root:
            return parent.name
    # 같은 스키마인데 이름도 위치도 제각각이면, 첫 파일 이름에 개수를 붙여 구분합니다.
    return f"{sorted(stems)[0]}_외{len(files) - 1}"


def _parquet_columns(files: Sequence[Path]) -> dict[str, str]:
    """parquet 스키마를 컬럼명 -> 타입 문자열로."""
    dataset = ds.dataset([str(file) for file in files], format="parquet")
    return {name: str(typ) for name, typ in zip(dataset.schema.names, dataset.schema.types, strict=True)}


def _json_records(path: Path) -> list[dict[str, Any]]:
    """JSON 한 파일에서 레코드 목록을 꺼냅니다.

    공공데이터 표준 JSON 은 `{"fields": [...], "records": [...]}` 처럼 레코드가 한 겹
    안에 들어 있습니다. 최상위가 배열인 평범한 형태도 함께 받습니다.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = next(
            (payload[key] for key in JSON_RECORD_KEYS if isinstance(payload.get(key), list)),
            [],
        )
        if not rows:
            raise ValueError(
                f"{path.name}: 레코드 배열을 찾지 못했습니다. 최상위 키 {sorted(payload)[:8]} "
                f"중에 {JSON_RECORD_KEYS} 가 없습니다."
            )
    else:
        raise ValueError(f"{path.name}: JSON 최상위가 배열이나 객체가 아닙니다.")

    if not rows:
        raise ValueError(f"{path.name}: 객체로 된 레코드가 없습니다.")
    # 객체가 아닌 항목을 조용히 버리면 원본 몇 줄이 사라졌는지 아무도 모릅니다.
    # 적재 건수가 안 맞을 때 원인을 여기까지 되짚기가 어려워서, 인덱스를 붙여 알립니다.
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}: {index}번째 레코드가 JSON 객체가 아닙니다({type(row).__name__}).")
    return rows


def _csv_records(path: Path) -> list[dict[str, Any]]:
    """CSV/TSV 를 dict 목록으로. 공공데이터는 BOM 이 붙어 오는 일이 잦아 utf-8-sig 로 읽습니다."""
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]


def _columns_from_records(records: Sequence[dict[str, Any]], *, sample: int = 50) -> dict[str, str]:
    """앞쪽 몇 건의 키를 모아 컬럼 목록을 만듭니다."""
    columns: dict[str, str] = {}
    for row in records[:sample]:
        for key, value in row.items():
            columns.setdefault(key, type(value).__name__)
    return columns


def _jsonl_columns(path: Path, *, sample: int = 50) -> dict[str, str]:
    """JSONL 은 스키마가 없어서 앞쪽 몇 줄의 키를 모아 추론합니다."""
    columns: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= sample:
                break
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                for key, value in payload.items():
                    columns.setdefault(key, type(value).__name__)
    return columns


def discover_datasets(path: Path) -> list[RawDataset]:
    """경로에서 데이터셋들을 찾습니다. parquet 은 스키마가 같은 파일끼리 묶입니다."""
    if not path.exists():
        raise FileNotFoundError(f"경로가 없습니다: {path}")

    files = _visible_files(path)
    parquet_files = [file for file in files if file.suffix.lower() in PARQUET_SUFFIXES]
    jsonl_files = [file for file in files if file.suffix.lower() in JSONL_SUFFIXES]
    json_files = [file for file in files if file.suffix.lower() in JSON_SUFFIXES]
    csv_files = [file for file in files if file.suffix.lower() in CSV_SUFFIXES]

    if not (parquet_files or jsonl_files or json_files or csv_files):
        raise FileNotFoundError(f"읽을 raw 파일을 찾지 못했습니다: {path} (parquet / JSONL / JSON / CSV)")

    root = path if path.is_dir() else path.parent
    datasets: list[RawDataset] = []

    # 스키마 지문이 같은 parquet 끼리 묶습니다.
    # 지문에 타입까지 넣는 이유: 컬럼명은 같고 타입만 다른 파일이 실제로 있습니다
    # (foodkeeper_product 는 수치형, foodkeeper_xls_product 는 전부 문자열인 같은 데이터).
    # 이름만으로 묶으면 같은 내용이 두 배로 들어갑니다.
    groups: dict[tuple[tuple[str, str], ...], list[Path]] = {}
    for file in parquet_files:
        fingerprint = tuple(sorted(_parquet_columns([file]).items()))
        groups.setdefault(fingerprint, []).append(file)

    for members in groups.values():
        members.sort()
        columns = _parquet_columns(members)
        row_count = ds.dataset([str(file) for file in members], format="parquet").count_rows()
        datasets.append(
            RawDataset(
                name=_dataset_name(members, root),
                files=tuple(members),
                fmt="parquet",
                columns=columns,
                row_count=row_count,
            )
        )

    for file in jsonl_files:
        with file.open(encoding="utf-8") as handle:
            row_count = sum(1 for line in handle if line.strip())
        datasets.append(
            RawDataset(
                name=file.stem,
                files=(file,),
                fmt="jsonl",
                columns=_jsonl_columns(file),
                row_count=row_count,
            )
        )

    for file in json_files:
        records = _json_records(file)
        datasets.append(
            RawDataset(
                name=file.stem,
                files=(file,),
                fmt="json",
                columns=_columns_from_records(records),
                row_count=len(records),
            )
        )

    for file in csv_files:
        records = _csv_records(file)
        datasets.append(
            RawDataset(
                name=file.stem,
                files=(file,),
                fmt="csv",
                columns=_columns_from_records(records),
                row_count=len(records),
            )
        )

    datasets.sort(key=lambda item: item.name)
    return datasets


def iter_records(dataset: RawDataset, *, batch_size: int = DEFAULT_BATCH_SIZE) -> Iterator[RawRecord]:
    """데이터셋의 행을 원본 dict 그대로 흘려 보냅니다."""
    row_index = 0
    if dataset.fmt == "parquet":
        arrow = ds.dataset([str(file) for file in dataset.files], format="parquet")
        for batch in arrow.to_batches(batch_size=batch_size):
            for payload in batch.to_pylist():
                yield RawRecord(dataset=dataset.name, row_index=row_index, payload=payload)
                row_index += 1
        return

    if dataset.fmt in ("json", "csv"):
        reader = _json_records if dataset.fmt == "json" else _csv_records
        for file in dataset.files:
            for payload in reader(file):
                yield RawRecord(dataset=dataset.name, row_index=row_index, payload=payload)
                row_index += 1
        return

    for file in dataset.files:
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
                yield RawRecord(dataset=dataset.name, row_index=row_index, payload=payload)
                row_index += 1


def _shorten(value: Any) -> Any:
    """샘플 행의 긴 값을 잘라 토큰을 아낍니다."""
    if isinstance(value, str) and len(value) > SAMPLE_VALUE_LIMIT:
        return value[:SAMPLE_VALUE_LIMIT] + " …(생략)"
    return value


def sample_rows(dataset: RawDataset, *, limit: int = DEFAULT_SAMPLE_ROWS) -> list[dict[str, Any]]:
    """1단계 프롬프트에 넣을 샘플 행."""
    rows: list[dict[str, Any]] = []
    for record in iter_records(dataset, batch_size=max(limit, 8)):
        rows.append({key: _shorten(value) for key, value in record.payload.items()})
        if len(rows) >= limit:
            break
    return rows


def render_dataset_brief(dataset: RawDataset, *, limit: int = DEFAULT_SAMPLE_ROWS) -> str:
    """LLM 에게 보여 줄 데이터셋 요약(스키마 + 샘플 행)."""
    columns = "\n".join(f"  - {name}: {typ}" for name, typ in dataset.columns.items())
    rows = json.dumps(sample_rows(dataset, limit=limit), ensure_ascii=False, indent=2, default=str)
    return (
        f"데이터셋 이름: {dataset.name}\n"
        f"파일 형식: {dataset.fmt} ({len(dataset.files)}개 파일)\n"
        f"행 수: {dataset.row_count}\n"
        f"컬럼:\n{columns}\n"
        f"샘플 행 {min(limit, dataset.row_count)}건:\n{rows}"
    )


def render_sibling_catalog(datasets: Sequence[RawDataset], *, exclude: str) -> str:
    """같은 폴더의 다른 데이터셋 목록(이름 + 행수 + 컬럼).

    이름만으로는 중복 사본을 알아낼 수 없습니다. 실제로 `foodkeeper_product` 와
    `foodkeeper_xls_product` 는 컬럼 38개가 같고 행수도 661 로 같은데 이름만 다릅니다.
    반대로 `korean_recipe_ingredients` 와 `korean_recipe_steps` 는 `recipe_name` 을
    공유하는 서로 다른 반쪽입니다. 둘 다 컬럼 구성을 봐야 판단할 수 있어서
    스키마까지 넣습니다.
    """
    others = [dataset for dataset in datasets if dataset.name != exclude]
    if not others:
        return "(다른 데이터셋 없음)"
    return "\n".join(f"- {dataset.name} ({dataset.row_count}행): {', '.join(dataset.columns)}" for dataset in others)


def preview_raw_source(path: Path, *, limit: int = 3) -> str:
    """CLI inspect 용 요약. 어떤 데이터셋이 몇 개 잡혔는지 확인합니다."""
    datasets = discover_datasets(path)
    lines = [f"경로     : {path}", f"데이터셋 : {len(datasets)}개", ""]
    for dataset in datasets:
        lines.append(dataset.describe())
        lines.append(f"  컬럼: {', '.join(dataset.columns)}")
        for row in sample_rows(dataset, limit=limit):
            preview = {key: str(value)[:40] for key, value in list(row.items())[:6]}
            lines.append(f"  샘플: {preview}")
        lines.append("")
    return "\n".join(lines)
