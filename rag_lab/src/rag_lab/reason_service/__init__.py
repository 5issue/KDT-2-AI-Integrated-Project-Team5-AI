"""추천 이유 생성 서비스. **서빙이 import 해 쓰는 운영 코드**입니다.

``rag_lab`` 의 기본 의존성은 ``httpx`` 하나이고, LangGraph·OpenAI SDK 는 ``rag`` 그룹이라 서빙에
딸려 가지 않습니다. 이 패키지 안에서는 상대 import 만 쓰고 ``rag_lab`` 의 다른 모듈을 부르지 않습니다.
서빙은 ``recsys_sql`` 과 같은 방식으로 ``rag-lab`` 을 워크스페이스 의존성에 추가하고 import 합니다.

서빙 라우터에서의 사용::

    results = await generate_reasons_for_rows(rows, client, timeout_seconds=2.0, vocabulary=vocab)
    for item, result in zip(items, results, strict=True):
        item.recommendation_reason = result.text

``client`` 가 ``None`` 이면 LLM 을 부르지 않고 전부 규칙 기반 문구를 돌려줍니다.
``vocabulary`` 는 ``SELECT name FROM ingredient`` 결과로, 앱 시작 때 한 번 읽어 넘깁니다.

결정 사항(2026-09-22 캐러셀 생성 방식 실험):
- 카드마다 LLM 1회 호출, 한 요청의 카드는 동시에 생성
- 카드별 제한 시간(기본 2초) + 자동 사실성 검사 + 실패한 카드만 규칙 기반 문구로 대체
- 모델 ``google/gemini-3.5-flash-lite``, OpenRouter 경유, 추론 최소(``minimal``)

문구 내용(프롬프트)은 별도 실험 대상입니다. ``prompt.py`` 의 상수만 바꾸면 됩니다.
"""

from .checks import Check, check_reason
from .client import OpenRouterReasonClient, ReasonSettings
from .facts import RecipeFacts, facts_from_row
from .service import ReasonResult, generate_reasons, generate_reasons_for_rows
from .template import template_reason

__all__ = [
    "Check",
    "OpenRouterReasonClient",
    "ReasonResult",
    "ReasonSettings",
    "RecipeFacts",
    "check_reason",
    "facts_from_row",
    "generate_reasons",
    "generate_reasons_for_rows",
    "template_reason",
]
