"""서빙 로그 출력 설정.

uvicorn 은 자기 로거(`uvicorn`, `uvicorn.error`, `uvicorn.access`)에만 핸들러를 답니다.
이 설정이 없으면 `serving` 과 `rag_lab` 의 INFO 로그는 어디에도 나가지 않습니다.
구조화 액세스 로그(`serving.access`), graceful shutdown 로그, 추천 이유 LLM 사용 여부와
실패 사유가 전부 여기에 해당합니다. WARNING 이상만 파이썬의 최후 수단 출력으로 겨우 나갑니다.

`caplog` 는 로거 레벨을 직접 올려 통과하므로 테스트로는 이 공백을 잡지 못합니다.
실제 프로세스에서 나가는지는 `test_request_log.py` 가 subprocess 로 확인합니다.
"""

from __future__ import annotations

import logging
import sys

from serving.request_log import access_logger

LOGGER_NAMES = ("serving", "rag_lab")
UVICORN_ACCESS_LOGGER = "uvicorn.access"


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
    """두 로거를 INFO 로 stderr 에 내고, uvicorn 의 평문 액세스 로그를 끕니다. 여러 번 불러도 같습니다.

    - 밖에서 이미 핸들러를 단 로거(uvicorn `--log-config` 로 `serving` 을 직접 설정한 경우)는 건드리지
      않습니다. 수집 형식을 바꾸려면 두 로거에 핸들러를 직접 지정하면 됩니다.
    - 핸들러를 달 때는 루트로 전파하지 않습니다. 루트에도 핸들러가 있으면(`basicConfig`, 루트만 설정한
      `--log-config`) 한 줄이 두 번, 그중 하나는 루트 형식으로 나가 액세스 로그의 JSON 한 줄 계약이
      깨지기 때문입니다. 테스트의 `caplog` 는 루트에서 받으므로 각 테스트 conftest 가 전파를 다시 켭니다.
    - 액세스 로그는 앱이 JSON 한 줄로 남깁니다(request_log.py). uvicorn 의 평문 액세스 로그는 요청마다
      같은 내용을 한 줄 더 찍고, 앱이 일부러 빼는 헬스체크 프로브도 찍습니다. 실행 플래그
      (`--no-access-log`)에만 맡기면 매니페스트가 command 를 바꿀 때 조용히 되살아나므로 여기서 끕니다.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter())
    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
        if not logger.handlers:
            logger.addHandler(handler)
            logger.propagate = False
    logging.getLogger(UVICORN_ACCESS_LOGGER).disabled = True
