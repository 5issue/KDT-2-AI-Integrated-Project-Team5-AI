"""공통 응답 envelope (백엔드 Spring ApiResponse 와 동일 계약).

모든 필드는 null 이어도 항상 직렬화되고, timestamp 는 백엔드의
@JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss'Z'", timezone = "UTC") 와
바이트 단위로 같은 포맷을 냅니다. FE 가 파서 하나로 양쪽 API 를 처리하기 위한 조건입니다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_serializer

T = TypeVar("T")

DEFAULT_SUCCESS_MESSAGE = "요청에 성공하였습니다."


class ErrorCode(Enum):
    """백엔드 GlobalErrorCode 미러 + AI 파트 확장 코드(429/503).

    응답에는 enum 이름만 나갑니다. http_status 는 exception handler 가 씁니다.
    """

    INVALID_INPUT_VALUE = (400, "잘못된 요청입니다.")
    UNAUTHORIZED = (401, "인증이 필요합니다.")
    FORBIDDEN = (403, "접근 권한이 없습니다.")
    RESOURCE_NOT_FOUND = (404, "요청한 리소스를 찾을 수 없습니다.")
    METHOD_NOT_ALLOWED = (405, "지원하지 않는 HTTP 메서드입니다.")
    CONFLICT = (409, "요청이 현재 서버 상태와 충돌합니다.")
    TOO_MANY_REQUESTS = (429, "요청 횟수를 초과했습니다. 잠시 후 다시 시도해주세요.")
    INTERNAL_SERVER_ERROR = (500, "서버 내부 오류가 발생했습니다.")
    SERVICE_UNAVAILABLE = (503, "서비스를 일시적으로 이용할 수 없습니다.")

    def __init__(self, http_status: int, message: str) -> None:
        self.http_status = http_status
        self.message = message


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApiResponse(BaseModel, Generic[T]):
    """{status, message, data, error, timestamp} 5필드 고정 envelope."""

    status: Literal["SUCCESS", "ERROR"]
    message: str
    data: T | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=_utc_now)

    @field_serializer("timestamp")
    def _serialize_timestamp(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def success(cls, data: T | None = None, message: str = DEFAULT_SUCCESS_MESSAGE) -> ApiResponse[T]:
        return cls(status="SUCCESS", message=message, data=data, error=None)

    @classmethod
    def failure(cls, code: ErrorCode, message: str | None = None) -> ApiResponse[T]:
        return cls(
            status="ERROR",
            message=message if message is not None else code.message,
            data=None,
            error=code.name,
        )
