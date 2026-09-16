"""전역 exception handler.

/api/v1 아래의 에러 응답을 공통 envelope 로 변환합니다. 헬스체크 등 prefix 밖 경로는
합의대로 envelope 적용 대상에서 제외하고 FastAPI 기본 응답을 그대로 둡니다.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from serving.envelope import ApiResponse, ErrorCode

API_PREFIX = "/api/v1"

_STATUS_TO_CODE = {code.http_status: code for code in ErrorCode}


def _is_api_request(request: Request) -> bool:
    # startswith 만 쓰면 /api/v10 같은 유사 prefix 도 걸립니다. 경로 경계까지 봅니다.
    path = request.url.path
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def _envelope_response(status_code: int, code: ErrorCode, message: str | None = None) -> JSONResponse:
    payload: ApiResponse[None] = ApiResponse.failure(code, message)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    if not _is_api_request(request):
        return await http_exception_handler(request, exc)

    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_SERVER_ERROR)
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else None
    return _envelope_response(exc.status_code, code, message)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    if not _is_api_request(request):
        return await request_validation_exception_handler(request, exc)

    return _envelope_response(422, ErrorCode.INVALID_INPUT_VALUE)


async def _handle_unexpected(request: Request, exc: Exception) -> Response:
    """처리되지 않은 예외의 마지막 방어선.

    예외 메시지에 DSN 등이 들어 있을 수 있어 응답에는 아무 상세도 싣지 않습니다.
    트레이스백은 응답 후 재발생(re-raise)되어 서버 로그에 남습니다.
    """
    if not _is_api_request(request):
        return PlainTextResponse("Internal Server Error", status_code=500)

    return _envelope_response(500, ErrorCode.INTERNAL_SERVER_ERROR)


def register_exception_handlers(app: FastAPI) -> None:
    """앱에 envelope 변환 핸들러를 붙입니다."""
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected)
