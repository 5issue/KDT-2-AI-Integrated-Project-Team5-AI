"""OpenRouter 호출. OpenAI 호환 ``/chat/completions`` 를 ``httpx`` 로 직접 부릅니다.

SDK 를 쓰지 않는 이유는 서빙에 딸려 가는 의존성을 ``httpx`` 하나로 두기 위해서입니다.
API 키는 요청 헤더에만 쓰고 로그나 예외 메시지에 넣지 않습니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_MODEL = "google/gemini-3.5-flash-lite"
BASE_URL = "https://openrouter.ai/api/v1"
MAX_OUTPUT_TOKENS = 256
# 실험의 Vertex `MINIMAL` 과 같은 조건. OpenRouter 의 이 모델은 `none` 을 거부합니다(400).
REASONING_EFFORT = "minimal"


class ReasonClientError(RuntimeError):
    """응답이 없거나 형식이 어긋난 경우. 호출한 쪽은 템플릿으로 대체합니다."""


@dataclass(frozen=True, slots=True)
class ReasonSettings:
    """환경변수로 받는 설정. pydantic 을 쓰지 않아 어디에 복사해도 동작합니다."""

    api_key: str = field(repr=False)  # 로그·예외에 설정 객체가 찍혀도 키는 안 보이게
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> ReasonSettings:
        """``OPENROUTER_API_KEY`` 가 비어 있으면 값 노출 없이 실패합니다."""
        env = os.environ if environ is None else environ
        api_key = env.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY 가 비어 있습니다.")
        return cls(
            api_key=api_key,
            model=env.get("REASON_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        )


class OpenRouterReasonClient:
    """문구 하나를 만드는 호출 하나. ``httpx.AsyncClient`` 는 앱이 만들어 넘기고 수명도 앱이 관리합니다."""

    def __init__(self, http: httpx.AsyncClient, settings: ReasonSettings) -> None:
        self._http = http
        self._settings = settings

    async def complete(self, system: str, user: str) -> str:
        # 제한 시간은 여기 두지 않습니다. 카드별 상한은 service.py 의 wait_for 하나가 정합니다.
        body: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            # 이 모델은 최소 추론 설정에서 추론 token 0 으로 응답했고(실험과 동일), 추론이 지연의 주원인이었습니다.
            "reasoning": {"effort": REASONING_EFFORT},
        }
        response = await self._http.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {self._settings.api_key}"},
            json=body,
        )
        if response.status_code >= 400:
            raise ReasonClientError(f"openrouter_http_{response.status_code}")
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ReasonClientError("openrouter_bad_response") from exc
        if not isinstance(content, str) or not content.strip():
            raise ReasonClientError("openrouter_empty_content")
        return content.strip().strip("\"“”‘’'")
