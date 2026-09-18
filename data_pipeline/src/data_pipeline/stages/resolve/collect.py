"""LLM Batch 응답 수거.

받은 답을 `MatchResult` 로 바꿔 리포트에 합칩니다. 여기서 하는 판단은 둘입니다.

- **확신도가 임계값(`MATCH_MIN_CONFIDENCE`) 미만이면 채택하지 않습니다.**
  마스터를 오염시키느니 미매칭으로 남겨 사람이 보게 하는 편이 낫습니다.
- 대표 하나에 온 답을 같은 클러스터의 다른 표기로 퍼뜨립니다(`_expand_delegates`).
  `eggs` 가 `달걀` 로 붙으면 `계란` 도 같이 붙습니다.
"""

from __future__ import annotations

import json
from typing import Any

from data_pipeline.batch.client import BatchRunner
from data_pipeline.config import Settings, get_settings
from data_pipeline.domain import ingredient_match_key
from data_pipeline.schemas import IngredientMatch, IngredientMatchBatch
from data_pipeline.stages import STAGE_RESOLVE
from data_pipeline.stages.resolve.models import MatchResult, ResolveReport
from data_pipeline.stages.resolve.report import save_report


def _allowed_master_ids(runner: BatchRunner, job_name: str) -> set[int] | None:
    """이 작업의 요청에 후보로 제시한 마스터 id 집합.

    `run_resolve` 가 요청을 만들 때 남깁니다. 옛 작업에는 파일이 없으므로 그때는
    `None` 을 돌려주고 검사를 건너뜁니다(있는 결과를 못 쓰게 만들지 않습니다).
    """
    path = runner.requests_dir / f"{job_name}_master_ids.json"
    if not path.exists():
        return None
    return {int(value) for value in json.loads(path.read_text(encoding="utf-8"))}


def _rejection(match: IngredientMatch, allowed: set[int] | None, min_confidence: float) -> str | None:
    """채택하지 않을 이유. 채택해도 되면 `None`.

    id 검사가 필요한 이유는 스키마가 임의의 정수를 허용하기 때문입니다. 제시하지 않은
    id 가 통과하면 `ingredient_matches.json` 을 거쳐 `recipe_ingredient` 까지 내려가
    **엉뚱한 재료에 조용히 연결**됩니다. (코드래빗 리뷰 PR #16)
    """
    if match.ingredient_id is None:
        return "매칭 없음"
    if match.confidence < min_confidence:
        return "확신도 미달"
    if allowed is not None and match.ingredient_id not in allowed:
        return f"후보에 없는 ingredient_id({match.ingredient_id})"
    return None


def _build_aliases(runner: BatchRunner, job_name: str, requested: set[str]) -> dict[str, str]:
    """요청에 함께 실어 보낸 표기들 -> 원래 매칭 키.

    모델이 `normalized_name` 대신 `hint_display` 를 돌려주는 일이 실제로 있었습니다.
    (`리큐르` 를 물었는데 `오렌지 풍미 리큐르` 로 답함) 그대로 두면 어느 요청에 대한
    답인지 몰라 버려지고, 그 재료는 매칭 기회 자체를 잃습니다.
    """
    aliases: dict[str, str] = {}
    for path in sorted(runner.requests_dir.glob(f"{job_name}_part*_input.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            body = json.loads(line).get("body", {})
            messages = body.get("messages", [])
            if len(messages) < 2:
                continue
            content = str(messages[1].get("content", ""))
            if "<data>" not in content:
                continue
            block = content.split("<data>", 1)[1].split("</data>", 1)[0]
            for item in json.loads(block):
                key = str(item.get("normalized_name", ""))
                if key not in requested:
                    continue
                for field_name in ("hint_display", "hint_raw_text", "display_name", "sample_raw_text"):
                    alias = ingredient_match_key(str(item.get(field_name) or ""))
                    if alias and alias not in requested:
                        aliases.setdefault(alias, key)
    return aliases


def _expand_delegates(
    runner: BatchRunner,
    job_name: str,
    report: ResolveReport,
    still_unmatched: list[dict[str, Any]],
    occurrences: dict[str, int],
) -> list[dict[str, Any]]:
    """대표 -> 변형 매핑을 읽어 결과를 나눠 줍니다. 매핑 파일이 없으면 그대로 둡니다."""
    path = runner.requests_dir / f"{job_name}_delegates.json"
    if not path.exists():
        return still_unmatched

    delegate: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    by_head = {item.normalized_name: item for item in report.llm}
    failed = {item["normalized_name"] for item in still_unmatched}

    for variant, head in sorted(delegate.items()):
        if variant == head or variant in by_head:
            continue
        matched = by_head.get(head)
        if matched is not None:
            report.llm.append(
                MatchResult(
                    normalized_name=variant,
                    ingredient_id=matched.ingredient_id,
                    matched_name=matched.matched_name,
                    method="cluster",
                    confidence=matched.confidence,
                )
            )
        elif head in failed and variant not in failed:
            still_unmatched.append(
                {
                    "normalized_name": variant,
                    "occurrence": occurrences.get(variant, 0),
                    "confidence": 0.0,
                    "reason": f"대표 {head} 가 매칭되지 않음",
                }
            )
    return still_unmatched


def collect(
    job_name: str,
    *,
    report: ResolveReport,
    settings: Settings | None = None,
) -> ResolveReport:
    """LLM 매칭 결과를 읽어 리포트에 반영합니다. 확신도 미만은 미매칭으로 남깁니다."""
    settings = settings or get_settings()
    runner = BatchRunner(STAGE_RESOLVE, settings)
    key_map = json.loads((runner.requests_dir / f"{job_name}_keys.json").read_text(encoding="utf-8"))
    requested = {name for names in key_map.values() for name in names}
    aliases = _build_aliases(runner, job_name, requested)
    allowed = _allowed_master_ids(runner, job_name)
    occurrences = {item["normalized_name"]: item.get("occurrence", 0) for item in report.unmatched}

    outcome = runner.parse(job_name, IngredientMatchBatch)
    decided: set[str] = set()
    still_unmatched: list[dict[str, Any]] = []

    for _, parsed in outcome.records:
        assert isinstance(parsed, IngredientMatchBatch)
        for match in parsed.matches:
            # LLM 이 공백을 넣거나 빼서 돌려줄 수 있으므로 보낼 때와 같은 키로 되돌립니다.
            # normalized_name 대신 display 표기로 답하는 경우도 있어 별칭까지 봅니다.
            name = ingredient_match_key(match.source_name)
            name = name if name in requested else aliases.get(name, name)
            if name not in requested or name in decided:
                continue
            decided.add(name)
            reason = _rejection(match, allowed, settings.match_min_confidence)
            # id 조건은 `_rejection` 이 이미 봅니다. 여기 한 번 더 두는 것은 타입 좁히기용입니다.
            if reason is None and match.ingredient_id is not None:
                report.llm.append(
                    MatchResult(
                        normalized_name=name,
                        ingredient_id=match.ingredient_id,
                        matched_name=match.matched_name or "",
                        method="llm",
                        confidence=match.confidence,
                    )
                )
            else:
                still_unmatched.append(
                    {
                        "normalized_name": name,
                        "occurrence": occurrences.get(name, 0),
                        "confidence": match.confidence,
                        "reason": match.reason or reason,
                    }
                )

    for name in sorted(requested - decided):
        still_unmatched.append(
            {"normalized_name": name, "occurrence": occurrences.get(name, 0), "confidence": 0.0, "reason": "응답 누락"}
        )

    # 대표로 물어본 결과를 같은 클러스터의 변형들에 되돌려줍니다.
    # 대표만 붙고 변형이 빠지면 그 레시피들의 재료가 통째로 사라집니다.
    still_unmatched = _expand_delegates(runner, job_name, report, still_unmatched, occurrences)

    report.unmatched = sorted(still_unmatched, key=lambda item: (-item["occurrence"], item["normalized_name"]))
    # collect 를 다시 돌리면 이전 결과가 리스트에 겹쳐 쌓입니다. 저장은 dict 라 값이
    # 정확하지만 화면 숫자가 부풀려져 매칭률을 잘못 읽게 됩니다.
    report.exact = _dedupe(report.exact)
    report.llm = _dedupe(report.llm)
    save_report(report, settings)
    return report


def _dedupe(items: list[MatchResult]) -> list[MatchResult]:
    """이름당 하나만 남깁니다. 먼저 들어온 것을 우선합니다."""
    seen: dict[str, MatchResult] = {}
    for item in items:
        seen.setdefault(item.normalized_name, item)
    return list(seen.values())
