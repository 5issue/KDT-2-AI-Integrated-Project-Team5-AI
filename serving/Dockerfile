# syntax=docker/dockerfile:1

# 빌드 컨텍스트는 **레포 루트**입니다 (serving 이 recsys_sql 을 워크스페이스 의존성으로
# 받기 때문). 루트 .dockerignore 가 .env 등 비밀값을 걸러 줍니다.
#
#   docker build -f serving/Dockerfile -t serving .
#   docker run --rm -p 8000:8000 -e DATABASE_URL=... serving

# --- 빌드 스테이지: uv 로 락파일 그대로 설치 --------------------------------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 의존성 레이어를 소스와 분리해 캐시를 살립니다. 락파일 해석에는 워크스페이스
# 멤버 4개의 pyproject 가 모두 필요합니다 (소스는 아직 필요 없음).
COPY pyproject.toml uv.lock ./
COPY serving/pyproject.toml serving/pyproject.toml
COPY recsys_sql/pyproject.toml recsys_sql/pyproject.toml
COPY rag_lab/pyproject.toml rag_lab/pyproject.toml
COPY data_pipeline/pyproject.toml data_pipeline/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package serving

# 소스는 serving 과 그 의존성인 recsys_sql 만 복사합니다.
# recsys_sql 의 SQL 카탈로그는 패키지 안(src/recsys_sql/queries)에 있어 휠에 담깁니다.
COPY serving/ serving/
COPY recsys_sql/ recsys_sql/
RUN uv sync --frozen --no-dev --no-editable --package serving

# --- 런타임 스테이지: venv 만 가져갑니다 -------------------------------------
FROM python:3.11-slim-bookworm

# 컨테이너에는 .env 를 넣지 않습니다. 환경변수로 주면 pydantic-settings 가 읽습니다.
# ENVIRONMENT 기본값을 prod 로 두어 /docs 와 /openapi.json 이 닫힌 채로 뜹니다.
ENV PYTHONUNBUFFERED=1 \
    ENVIRONMENT=prod

RUN groupadd --system serving && useradd --system --gid serving serving

COPY --from=builder /app/.venv /app/.venv

USER serving
EXPOSE 8000

# liveness 만 봅니다. DB 상태(/health/db)로 컨테이너를 재시작시키지 않기 위해서입니다.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["/app/.venv/bin/uvicorn", "serving.app:app", "--host", "0.0.0.0", "--port", "8000"]
