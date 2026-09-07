"""Batch API 결과 JSONL 을 읽어 Pydantic 으로 검증합니다.

결과 한 줄의 모양:
{"custom_id": "...", "response": {"status_code": 200, "body": {...chat completion...}}, "error": null}
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from data_pipeline.schemas import BatchParseFailure, ParsedRecipe


@dataclass(slots=True)
class ParsedRecipeRecord:
    """검증까지 끝난 레시피 한 건."""

    source_id: str
    recipe: ParsedRecipe


@dataclass(slots=True)
class ParseOutcome:
    """결과 파일 전체를 훑은 뒤의 성공/실패 집계."""

    records: list[ParsedRecipeRecord] = field(default_factory=list)
    failures: list[BatchParseFailure] = field(default_factory=list)

    @property
    def total(self) -> int:
        """처리한 줄 수."""
        return len(self.records) + len(self.failures)

    def summary(self) -> str:
        """한 줄 요약."""
        return f"성공 {len(self.records)}건 / 실패 {len(self.failures)}건 (총 {self.total}건)"


def iter_result_lines(path: Path) -> Iterator[dict[str, object]]:
    """output/error JSONL 을 한 줄씩 읽습니다. 디렉터리를 주면 *_output.jsonl 을 모두 읽습니다."""
    files = sorted(path.glob("*_output.jsonl")) if path.is_dir() else [path]
    if not files:
        raise FileNotFoundError(f"배치 결과 JSONL 을 찾지 못했습니다: {path}")
    for file in files:
        with file.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _extract_content(line: dict[str, object]) -> tuple[str | None, BatchParseFailure | None]:
    """결과 한 줄에서 모델 출력 문자열만 꺼냅니다. 실패면 사유를 돌려줍니다."""
    custom_id = str(line.get("custom_id", "<unknown>"))

    if line.get("error"):
        return None, BatchParseFailure(custom_id=custom_id, reason="batch_error", detail=str(line["error"])[:500])

    response = line.get("response")
    if not isinstance(response, dict):
        return None, BatchParseFailure(custom_id=custom_id, reason="missing_response")

    status_code = response.get("status_code")
    if status_code != 200:
        return None, BatchParseFailure(custom_id=custom_id, reason="http_error", detail=str(status_code))

    body = response.get("body")
    if not isinstance(body, dict):
        return None, BatchParseFailure(custom_id=custom_id, reason="missing_body")

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, BatchParseFailure(custom_id=custom_id, reason="empty_choices")

    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        return None, BatchParseFailure(custom_id=custom_id, reason="bad_message")

    if message.get("refusal"):
        return None, BatchParseFailure(custom_id=custom_id, reason="refusal", detail=str(message["refusal"])[:500])

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, BatchParseFailure(custom_id=custom_id, reason="empty_content")

    return content, None


def parse_recipe_results(path: Path) -> ParseOutcome:
    """레시피 파싱 배치 결과를 ParsedRecipe 로 검증합니다."""
    outcome = ParseOutcome()
    for line in iter_result_lines(path):
        custom_id = str(line.get("custom_id", "<unknown>"))
        content, failure = _extract_content(line)
        if failure is not None:
            outcome.failures.append(failure)
            continue

        assert content is not None
        try:
            recipe = ParsedRecipe.model_validate_json(content)
        except ValidationError as exc:
            outcome.failures.append(
                BatchParseFailure(custom_id=custom_id, reason="schema_validation", detail=str(exc)[:500])
            )
            continue

        source_id = custom_id.split("::", 1)[-1]
        outcome.records.append(ParsedRecipeRecord(source_id=source_id, recipe=recipe))
    return outcome
