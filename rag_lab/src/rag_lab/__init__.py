"""LangGraph + pgvector 기반 RAG 실험 환경.

서빙이 부를 것은 `recommendation_reason` 하나입니다(계획 4-6).
그 함수는 `rag_lab.recommendation.core` 에 있고, 같은 패키지의 `checks`/`judge`/
`experiment` 는 실험용이라 서빙 경로에서 부르지 않습니다.
"""

from rag_lab.recommendation.core import RecommendationCase, recommendation_reason

__all__ = ["RecommendationCase", "__version__", "recommendation_reason"]

__version__ = "0.1.0"
