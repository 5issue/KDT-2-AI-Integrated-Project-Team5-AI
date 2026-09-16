"""공통 응답 envelope 테스트.

백엔드(Spring) ApiResponse 와 같은 JSON 모양을 보장하는 것이 목적입니다:
{status, message, data, error, timestamp} 5개 필드가 항상 나가고,
timestamp 는 초 단위 UTC(yyyy-MM-dd'T'HH:mm:ss'Z') 포맷입니다.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from serving.envelope import ApiResponse, ErrorCode

TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class _Item(BaseModel):
    recipe_id: int
    name: str


def test_success_envelope_shape() -> None:
    """성공 응답: status/message/error 규칙과 data 전달."""
    item = _Item(recipe_id=1, name="김치찌개")
    resp = ApiResponse.success(item)

    assert resp.status == "SUCCESS"
    assert resp.message == "요청에 성공하였습니다."
    assert resp.data == item
    assert resp.error is None


def test_success_without_data_keeps_null_data() -> None:
    """본문 없는 성공(DELETE 등)도 data: null 로 나갑니다. 백엔드 success() 와 동일."""
    resp = ApiResponse.success()

    assert resp.status == "SUCCESS"
    assert resp.data is None
    assert resp.error is None


def test_failure_envelope_uses_error_code_name_and_message() -> None:
    """에러 응답: error 는 코드 enum 이름 문자열, message 는 코드 기본 메시지."""
    resp = ApiResponse.failure(ErrorCode.RESOURCE_NOT_FOUND)

    assert resp.status == "ERROR"
    assert resp.error == "RESOURCE_NOT_FOUND"
    assert resp.message == "요청한 리소스를 찾을 수 없습니다."
    assert resp.data is None


def test_failure_message_override() -> None:
    """백엔드 error(ErrorCode, message) 오버로드와 동일하게 메시지를 바꿀 수 있습니다."""
    resp = ApiResponse.failure(ErrorCode.RESOURCE_NOT_FOUND, "Recipe not found")

    assert resp.error == "RESOURCE_NOT_FOUND"
    assert resp.message == "Recipe not found"


def test_json_always_includes_all_five_fields() -> None:
    """@JsonInclude(ALWAYS) 와 동일하게 null 필드도 항상 직렬화합니다."""
    payload = json.loads(ApiResponse.success().model_dump_json())

    assert set(payload.keys()) == {"status", "message", "data", "error", "timestamp"}
    assert payload["data"] is None
    assert payload["error"] is None


def test_timestamp_serializes_as_utc_seconds() -> None:
    """timestamp 는 마이크로초 없이 초 단위 'Z' 포맷이어야 합니다."""
    payload = json.loads(ApiResponse.success().model_dump_json())

    assert TIMESTAMP_PATTERN.match(payload["timestamp"]), payload["timestamp"]


def test_error_code_http_status_mapping() -> None:
    """핸들러가 쓸 HTTP status 매핑. 429/503 은 AI 파트 확장 코드입니다."""
    assert ErrorCode.INVALID_INPUT_VALUE.http_status == 400
    assert ErrorCode.UNAUTHORIZED.http_status == 401
    assert ErrorCode.FORBIDDEN.http_status == 403
    assert ErrorCode.RESOURCE_NOT_FOUND.http_status == 404
    assert ErrorCode.CONFLICT.http_status == 409
    assert ErrorCode.TOO_MANY_REQUESTS.http_status == 429
    assert ErrorCode.INTERNAL_SERVER_ERROR.http_status == 500
    assert ErrorCode.SERVICE_UNAVAILABLE.http_status == 503
