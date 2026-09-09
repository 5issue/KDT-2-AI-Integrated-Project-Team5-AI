KDT-2-AI-INTEGRATED-PROJECT-TEAM5 팀명: 5이쉬에 AI팀 깃허브입니다.

신선식품 쇼핑몰의 검색, 레시피 추천, My 레시피 제작을 담당하는 AI 파트 레포입니다.

## 폴더 구조

`uv` 워크스페이스이고, 루트의 폴더 4개가 각각 워크스페이스 멤버입니다.
**폴더마다 `.env` 를 따로 둡니다.** 시작할 때 각 폴더의 `.env.example` 을 복사하세요.

| 폴더 | 역할 | 주요 스택 |
| --- | --- | --- |
| [`data_pipeline/`](data_pipeline/README.md) | raw(스키마 제각각) -> LLM Batch 3단계(프로파일·추출·해석) -> Neon 적재 | openai, pyarrow, SQLAlchemy 2.0, asyncpg, alembic |
| [`recsys_sql/`](recsys_sql/README.md) | 추천 비즈니스 로직 SQL 카탈로그 + pytest 검증 | SQLAlchemy 2.0, asyncpg, pytest |
| [`rag_lab/`](rag_lab/README.md) | RAG 실험 환경 (라우팅 -> 검색 -> 생성) | LangGraph, pgvector, openai |
| [`serving/`](serving/README.md) | 추천 API 서빙 서버 | FastAPI, asyncpg |

```
raw 데이터 ─▶ data_pipeline ─▶ Neon PostgreSQL ─┬─▶ recsys_sql  (SQL 작성/검증)
                                                │        │ 검증 통과 후 promote
                                                │        ▼
                                                ├─▶ serving    (API 서빙)
                                                └─▶ rag_lab    (RAG 실험)
```

## 시작하기

```bash
uv sync --all-packages --all-groups
uv run pre-commit install

# 폴더별 .env 준비 (값은 각자 채우기)
cp data_pipeline/.env.example data_pipeline/.env
cp recsys_sql/.env.example   recsys_sql/.env
cp rag_lab/.env.example      rag_lab/.env
cp serving/.env.example      serving/.env

uv run data-pipeline check-db   # Neon 연결 확인
uv run recsys-sql   check-db
uv run rag-lab      check-db
uv run serving      check-db
```

## 검증

```bash
uv run ruff check .          # lint (CI 게이트)
uv run ruff format .         # CI 는 --check 로 검사
uv run pyright               # 타입 검사 (CI 게이트)
uv run pytest -q             # 전체 테스트
uv lock --check              # lockfile 동기화 (CI 게이트)
```

`DATABASE_URL` 이 없으면 `@pytest.mark.db` 테스트는 자동으로 skip 됩니다.
CI 는 DB 없이 도는 테스트만 돌립니다.

## 3명이 나눠 쓰는 방법

`recsys_sql` 과 `rag_lab` 은 각자 폴더를 파서 작업합니다.

```
recsys_sql/queries/<github_id>/*.sql        추천 SQL
rag_lab/experiments/<github_id>/            실험 질문 세트와 결과
```

각 폴더의 `_template/` 을 복사해서 시작하고, `.env` 의 `QUERY_OWNER` /
`EXPERIMENT_OWNER` 에 본인 github id 를 넣으면 기본 대상이 본인 폴더로 잡힙니다.
결과물이 각자 폴더로 갈리므로 세 명이 같은 파일을 건드릴 일이 없습니다.

**DB 도 각자 Neon 브랜치를 씁니다.** 콘솔에서 `main` 브랜치를 복제해 본인 브랜치를 만들고,
그 브랜치의 Connection Details 를 `.env` 에 넣으세요. 스키마 실험이나 인덱스 변경이
서로에게 번지지 않습니다.

## 이 레포에서 알아두면 좋은 것

**`python -m <패키지>` 대신 콘솔 스크립트를 쓰세요.** 레포 루트에 멤버와 같은 이름의
디렉터리(`data_pipeline/` 등)가 있어서, 루트에서 `python -m data_pipeline.cli` 를 하면
그 디렉터리가 네임스페이스 패키지로 잡혀 실패합니다.
`uv run data-pipeline`, `uv run recsys-sql`, `uv run rag-lab`, `uv run serving` 을 쓰면 됩니다.

**테스트 파일 이름은 레포 전체에서 유일해야 합니다.** pytest 기본(prepend) 임포트 모드에서는
폴더가 달라도 같은 basename 이면 모듈 이름이 충돌합니다. 그래서 연결 테스트가
`test_pipeline_db_connection.py` / `test_recsys_db_connection.py` / `test_rag_db_connection.py`
처럼 접두사를 달고 있습니다.

**Neon 연결에는 세 가지 처리가 필요합니다.** 각 폴더의 `db.py` 가 해 줍니다.
DSN 스킴 변환, asyncpg 가 모르는 `sslmode`/`channel_binding` 제거, 그리고 pooler
엔드포인트에서의 prepared statement 캐시 끄기(PgBouncer transaction 모드)입니다.
같은 처리가 폴더마다 복제되어 있는데, 폴더별로 독립 실행되게 하려는 의도적 선택입니다.
세 곳 이상에서 내용이 갈리기 시작하면 워크스페이스 멤버로 분리하는 편이 낫습니다.

## 문서

- [AGENTS.md](AGENTS.md) — AI 에이전트 작업 규칙
- [CLAUDE.md](CLAUDE.md) — Claude Code 용 안내
- [docs/](docs/README.md) — 사람끼리 공유하는 문서
- `ai_context/` — AI 코딩 맥락 (git 추적 제외)
