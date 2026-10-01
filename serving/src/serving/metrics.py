"""Prometheus 메트릭. kube-prometheus-stack 이 `/metrics` 를 긁어 갑니다.

`prometheus-fastapi-instrumentator` 의 기본 지표(요청 수, 지연 히스토그램, 요청·응답 크기)를 냅니다.

- `handler` 라벨은 경로 템플릿(`/api/v1/products/{product_id}`)입니다. id 마다 시계열이 늘지 않습니다.
  라우트에 맞지 않는 요청(스캐너의 404 등)은 세지 않습니다. 같은 이유입니다.
- 상태 코드는 묶지 않습니다. 503(DB 미연결)과 500(처리되지 않은 예외)을 갈라 봐야 하기 때문입니다.
- 헬스체크와 `/metrics` 자신은 세지 않습니다. 프로브·스크레이프가 요청 수와 지연 분포를 흐립니다.
- 처리되지 않은 예외도 500 으로 셉니다. 계측 미들웨어가 다른 미들웨어보다 바깥에 서므로 rate limit 의
  429 와 예외가 그대로 보입니다. 원인(traceback)은 지표가 아니라 로그에서 봅니다.
- 인증이 없습니다. 클러스터 안의 Prometheus 만 닿아야 합니다(배포 가이드의 NetworkPolicy·ServiceMonitor).
- 프로세스 메모리에 쌓는 지표라 uvicorn 워커를 여러 개 띄우면 워커별로 갈립니다. 이미지는 워커 하나입니다.
- 레지스트리는 앱마다 따로 둡니다. 전역 레지스트리를 쓰면 한 프로세스에 앱이 둘 이상일 때(테스트) 두 번째
  앱의 지표가 중복 등록으로 조용히 빠집니다. 전역 레지스트리가 기본으로 내는 프로세스·GC 지표는 직접 답니다.
"""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector
from prometheus_fastapi_instrumentator import Instrumentator

METRICS_PATH = "/metrics"
# 세지 않는 경로(정규식). 헬스체크 프로브와 스크레이프 자신입니다.
EXCLUDED_HANDLERS = [r"^/metrics$", r"^/health(/db)?$"]


def install_metrics(app: FastAPI) -> None:
    """계측 미들웨어를 달고 `/metrics` 를 엽니다. 다른 미들웨어를 모두 단 뒤에 불러야 가장 바깥에 섭니다."""
    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=EXCLUDED_HANDLERS,
        registry=registry,
    ).instrument(app).expose(app, endpoint=METRICS_PATH, include_in_schema=False)
