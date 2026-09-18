"""3단계 산출물(`ingredient_matches.json`) 저장과 읽기.

**이 파일이 매칭률의 16.4%p 를 혼자 들고 있습니다.** `.gitignore` 대상이라 없는 상태로
`load-recipes` 를 돌리면 79.2% 가 62.8% 로 떨어집니다. 지우기 전에 다시 만들 방법
(배치 재실행, 약 $0.1)이 있는지 확인하세요.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.config import Settings, get_settings
from data_pipeline.stages.resolve.models import MatchResult, ResolveReport


def matches_path(settings: Settings | None = None) -> Path:
    """3단계 중간 산출물 경로."""
    settings = settings or get_settings()
    return settings.artifacts_dir / "ingredient_matches.json"


def save_report(report: ResolveReport, settings: Settings | None = None) -> Path:
    """매칭 결과를 저장합니다. 적재 단계가 이 파일을 읽습니다."""
    settings = settings or get_settings()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = matches_path(settings)
    payload = {
        "matched": {
            item.normalized_name: {
                "ingredient_id": item.ingredient_id,
                "matched_name": item.matched_name,
                "method": item.method,
                "confidence": item.confidence,
            }
            for item in report.matched
        },
        "unmatched": report.unmatched,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def count_llm_matches(settings: Settings | None = None) -> int:
    """저장된 리포트에 LLM 이 채운 매칭이 몇 종인지. 없으면 0."""
    path = matches_path(settings)
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    return sum(1 for item in payload.get("matched", {}).values() if item.get("method") == "llm")


def load_report(settings: Settings | None = None) -> ResolveReport:
    """저장된 3단계 산출물을 리포트로 되읽습니다. collect 가 여기에 LLM 결과를 얹습니다."""
    path = matches_path(settings)
    if not path.exists():
        raise FileNotFoundError(f"매칭 결과가 없습니다: {path.name}. resolve 를 먼저 실행하세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    report = ResolveReport(unmatched=list(payload.get("unmatched", [])))
    for name, item in payload.get("matched", {}).items():
        result = MatchResult(
            normalized_name=name,
            ingredient_id=int(item["ingredient_id"]),
            matched_name=str(item.get("matched_name", "")),
            method=str(item.get("method", "exact")),
            confidence=float(item.get("confidence", 1.0)),
        )
        (report.exact if result.method == "exact" else report.llm).append(result)
    report.total_names = len(report.matched) + len(report.unmatched)
    return report


def load_matches(settings: Settings | None = None) -> dict[str, int]:
    """정규화 이름 -> ingredient_id."""
    path = matches_path(settings)
    if not path.exists():
        raise FileNotFoundError(f"매칭 결과가 없습니다: {path.name}. 3단계를 먼저 끝내세요.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {name: int(item["ingredient_id"]) for name, item in payload["matched"].items()}
