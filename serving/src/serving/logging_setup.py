"""서빙 로그 출력 설정.

uvicorn 은 자기 로거(`uvicorn`, `uvicorn.error`, `uvicorn.access`)에만 핸들러를 답니다.
이 설정이 없으면 `serving` 과 `rag_lab` 의 INFO 로그는 어디에도 나가지 않습니다.
구조화 액세스 로그(`serving.access`), graceful shutdown 로그, 추천 이유 LLM 사용 여부와
실패 사유가 전부 여기에 해당합니다. WARNING 이상만 파이썬의 최후 수단 출력으로 겨우 나갑니다.

테스트는 `caplog` 가 로거 레벨을 직접 올려 통과하므로 이 공백을 잡지 못했습니다.
실제 프로세스에서 나가는지는 `test_request_log.py` 가 subprocess 로 확인합니다.
"""

from __future__ import annotations

import logging
import sys

from serving.request_log import access_logger

LOGGER_NAMES = ("serving", "rag_lab")


class _Formatter(logging.Formatter):
    """액세스 로그는 수집기가 바로 파싱하도록 JSON 한 줄 그대로, 나머지는 수준과 로거 이름을 붙입니다."""

    def __init__(self) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")
        self._bare = logging.Formatter("%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if record.name == access_logger.name:
            return self._bare.format(record)
        return super().format(record)


def configure_logging() -> None:
    """두 로거를 INFO 로 stderr 에 냅니다. 여러 번 불러도 핸들러는 하나입니다.

    밖에서 이미 설정한 로거(uvicorn `--log-config` 등)는 건드리지 않습니다. 핸들러가 없을 때만
    달고, 레벨이 정해지지 않았을 때만 INFO 로 둡니다. `propagate` 는 그대로 두어 pytest 의
    `caplog`(루트에서 받음)가 계속 동작하게 합니다. 운영에서는 루트에 핸들러가 없어 중복되지 않습니다.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter())
    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
        if not logger.handlers:
            logger.addHandler(handler)
