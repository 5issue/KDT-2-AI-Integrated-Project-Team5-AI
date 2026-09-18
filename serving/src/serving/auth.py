"""사용자 식별 의존성.

Next.js BFF(서버)가 사용자 id 를 헤더로 실어 보내는 구조입니다. 공유 시크릿 검증
방식과 헤더 이름은 FE 와 협의 중이라, 확정되면 이 모듈만 바꾸면 되도록 한 곳에
모아 둡니다. FastAPI 가 외부에 노출될 가능성을 고려해 헤더 형식은 항상 검증합니다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

USER_ID_HEADER = "X-User-Id"


def get_current_user_id(
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> int:
    """X-User-Id 헤더에서 사용자 id 를 읽습니다. 없거나 형식이 틀리면 401 입니다."""
    if x_user_id is None or not x_user_id.isdigit() or int(x_user_id) < 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
        )
    return int(x_user_id)


CurrentUserId = Annotated[int, Depends(get_current_user_id)]
