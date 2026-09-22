"""LangGraph + pgvector 기반 RAG 실험 환경.

서빙이 부를 것은 `rag_lab.reason_service` 입니다.
그 하위 패키지는 표준 라이브러리와 ``httpx`` 만 쓰므로, **이 파일은 무거운 모듈을 미리 import 하지
않습니다.** 여기서 `rag_lab.recommendation.core` 를 끌어오면 OpenAI SDK 와 LangGraph 가 따라와
서빙에서 `import rag_lab.reason_service` 만 해도 설치되지 않은 패키지를 찾다 실패합니다.

옛 진입점 `RecommendationCase`, `recommendation_reason` 은 처음 접근할 때 늦게 불러옵니다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 타입 검사기에게만 보이고, 실행 시에는 아래 __getattr__ 가 늦게 불러옵니다.
    from rag_lab.recommendation.core import RecommendationCase, recommendation_reason

__all__ = ["RecommendationCase", "__version__", "recommendation_reason"]

__version__ = "0.1.0"

_LAZY = {"RecommendationCase", "recommendation_reason"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from rag_lab.recommendation import core

        return getattr(core, name)
    raise AttributeError(f"module 'rag_lab' has no attribute {name!r}")
