"""추천 문구 생성과 그 품질 검증.

네 갈래로 나뉩니다. **서빙이 쓰는 것은 `core` 하나뿐**이고 나머지는 실험용입니다.

| 모듈 | 역할 | 서빙 경로 |
| --- | --- | --- |
| `core` | 상황 -> 프롬프트 -> LLM 호출 | **씀** |
| `checks` | 재료 환각·보유 뒤집힘·시간 조작 검사 (LLM 무관) | 안 씀 |
| `judge` | 루브릭 8항목 채점 (LLM 판정자) | 안 씀 |
| `experiment` | 상황 세트 실행·JSONL 기록·재채점 | 안 씀 |

한 파일에 있던 것을 나눴습니다. 생성 로직 자체는 짧은데 실험용 검증·리포팅이 함께
있어서 760줄이 됐던 것입니다.

기존 `from rag_lab.recommendation import ...` 는 그대로 동작합니다. 다만 새로 쓰는
코드는 어느 층을 쓰는지 드러나게 **모듈을 직접 지목**하는 편이 좋습니다.

    from rag_lab.recommendation.core import recommendation_reason   # 서빙
    from rag_lab.recommendation.checks import check_reason          # 실험
"""

from rag_lab.recommendation.checks import Check, check_reason, collect_vocabulary
from rag_lab.recommendation.core import (
    MAX_REASON_CHARS,
    RECOMMENDATION_SYSTEM_PROMPT,
    RecommendationCase,
    build_situation,
    build_user_prompt,
    case_fingerprint,
    recommendation_reason,
)
from rag_lab.recommendation.experiment import (
    PriorReason,
    ReasonReport,
    ReasonResult,
    load_reasons,
    load_recommendation_cases,
    rescore_experiment,
    run_reason_experiment,
)
from rag_lab.recommendation.judge import (
    JUDGE_SYSTEM_PROMPT,
    PASS_MEAN_SCORE,
    RUBRIC_ITEMS,
    RubricScore,
    parse_rubric,
    score_reason,
)

__all__ = [
    "JUDGE_SYSTEM_PROMPT",
    "MAX_REASON_CHARS",
    "PASS_MEAN_SCORE",
    "RECOMMENDATION_SYSTEM_PROMPT",
    "RUBRIC_ITEMS",
    "Check",
    "PriorReason",
    "ReasonReport",
    "ReasonResult",
    "RecommendationCase",
    "RubricScore",
    "build_situation",
    "build_user_prompt",
    "case_fingerprint",
    "check_reason",
    "collect_vocabulary",
    "load_reasons",
    "load_recommendation_cases",
    "parse_rubric",
    "recommendation_reason",
    "rescore_experiment",
    "run_reason_experiment",
    "score_reason",
]
