"""추천 문구 테스트 보조. conftest 는 pytest 가 특별 취급하므로 임포트용은 여기 둡니다.

테스트가 `test_reco_core / checks / judge / experiment` 로 나뉘어 있어 공용 조립기를
한곳에 둡니다. 소스 쪽 `rag_lab/src/rag_lab/recommendation/` 분할과 같은 모양입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_lab.recommendation.checks import check_reason
from rag_lab.recommendation.core import RecommendationCase
from rag_lab.recommendation.judge import RUBRIC_ITEMS

CASES_DIR = Path(__file__).resolve().parents[1] / "experiments" / "openLeeWorld"


def case(**overrides: object) -> RecommendationCase:
    """기본 상황 하나. 필요한 필드만 덮어씁니다."""
    payload: dict = {
        "case_id": "t",
        "recipe": "두부조림",
        "match_rate": 0.5,
        "have": ["두부"],
        "missing": ["참기름"],
        "pantry": [],
        "cook_time_min": None,
    }
    payload.update(overrides)
    return RecommendationCase(**payload)


def failed(case_: RecommendationCase, reason: str, **kwargs: object) -> list[str]:
    """실패한 검사 이름만."""
    checks = check_reason(case_, reason, **kwargs)  # type: ignore[arg-type]
    return [check.name for check in checks if not check.passed]


def rubric_json(default: int = 8, **overrides: int) -> str:
    """판정자 응답 하나. 지정하지 않은 항목은 `default` 점."""
    scores = {key: overrides.get(key, default) for key, _, _ in RUBRIC_ITEMS}
    return json.dumps({**scores, "comment": "사유"}, ensure_ascii=False)
