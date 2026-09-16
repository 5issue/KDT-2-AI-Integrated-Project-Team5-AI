"""LangGraph + pgvector 기반 RAG 실험 환경.

서빙이 부를 것은 `recommendation_reason` 하나입니다(계획 4-6).
나머지는 실험용이라 직접 임포트해서 쓰세요.
"""

from rag_lab.recommendation import RecommendationCase, recommendation_reason

__all__ = ["RecommendationCase", "__version__", "recommendation_reason"]

__version__ = "0.1.0"
