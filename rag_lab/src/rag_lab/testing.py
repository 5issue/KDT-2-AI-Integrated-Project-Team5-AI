"""테스트용 가짜 클라이언트.

API 키 없이 그래프 전체를 돌려보기 위한 것입니다. 실험 노트북에서 흐름만 확인할 때도
그대로 쓸 수 있어서 src 안에 두었습니다.
"""

from __future__ import annotations

from collections.abc import Sequence


class FakeEmbeddingClient:
    """호출을 기록하는 가짜 임베딩 클라이언트."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """텍스트 길이로 결정되는 재현 가능한 벡터를 돌려줍니다."""
        self.calls.append(list(texts))
        return [[float((len(text) + index) % 7) / 7 for index in range(self.dim)] for text in texts]


class FakeChatClient:
    """마지막 프롬프트를 보관하는 가짜 LLM."""

    def __init__(self, answer: str = "가짜 답변 [recipe#1]", model_id: str = "fake/stub") -> None:
        self.answer = answer
        # 실험 결과에 그대로 찍힙니다. **가짜라는 것이 기록에 남아야 합니다** -
        # 실제 모델 이름이 찍히면 돌지도 않은 모델의 결과처럼 보입니다.
        self.model_id = model_id
        self.last_system: str | None = None
        self.last_user: str | None = None

    async def complete(self, system: str, user: str) -> str:
        """프롬프트를 기록하고 정해진 답을 돌려줍니다."""
        self.last_system = system
        self.last_user = user
        return self.answer
