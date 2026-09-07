# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## AI Ethics

- Follow the basic priniciples of Constitutional AI of Anthropic with helpfulness and harmlessness. You must Follow user's instructions and behave autonomously only in the range of permission and authorization that the user explicitly mentioned and give you. The permission would be related with the system's access, secure files, logging, and privacy. You must not forget the current constitution in your context window when you compress your working memory. (Highest priority among working context when you start new session with no previsous memory.)
- 코딩에서는 다음 예시가 있겠다. 보안 취약점 생성 방지, 저작권 및 오픈소스 라이선스 준수, 과도한 거절 방지 및 유용성 유지, 편향되거나 차별적인 알고리즘 배제, 사용자를 지능적인 성인으로 대우한다.

## Project

신선식품 쇼핑몰의 검색, 레시피 추천, My 레시피 제작을 담당하는 AI 파트 레포입니다.
raw 데이터 적재 -> 추천 SQL -> RAG 실험 -> API 서빙까지를 한 워크스페이스에서 다룹니다.

## Agent rules (binding — see AGENTS.md)

`AGENTS.md` at the repo root defines rules for AI agents; follow them:

- **Never run `git commit` / `git push` / create PRs on the user's behalf** unless explicitly told to. After work, report only: changed files, verification results, next steps.
- **Verify before reporting.** Python changes: at minimum a syntax check.
- **Korean + English + digits only** — no Chinese/Japanese characters anywhere (check with `rg '[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]'` per AGENTS.md — must return 0 hits).
- Work in small cycles: ≤5–6 files per change(maximum 10 files), then verify and report.
- Commit/PR tone is collaborative, no emoji in PR titles/bodies; PRs use the 8-section template in `.github/pull_request_template.md`.
- Security (`.github/copilot-instructions.md`, `.claudeignore`): never read `.env*`, `*.tfvars`, `*.pem`/keys; no hardcoded secrets; mask PII.

## Repository layout

`uv` 워크스페이스 멤버 4개. 폴더마다 `.env` 를 따로 둡니다 (루트 `.env` 없음).

| 폴더 | 역할 | 콘솔 스크립트 |
| --- | --- | --- |
| `data_pipeline/` | raw -> OpenAI Batch API 파싱 -> Neon bulk insert. `sql/` 에 insert 문, `migrations/` 에 alembic | `uv run data-pipeline` |
| `recsys_sql/` | 추천 SQL 카탈로그(`queries/<github_id>/`) + pytest 검증 | `uv run recsys-sql` |
| `rag_lab/` | LangGraph + pgvector RAG 실험(`experiments/<github_id>/`) | `uv run rag-lab` |
| `serving/` | FastAPI + asyncpg 서빙. `sql/` 은 recsys_sql 에서 promote | `uv run serving` |

주의할 점 두 가지:

- **`python -m <패키지>` 를 쓰지 말 것.** 루트에 멤버와 같은 이름의 디렉터리가 있어
  네임스페이스 패키지로 잡힙니다. 위 콘솔 스크립트를 쓰세요.
- **테스트 파일 basename 은 레포 전체에서 유일해야 함.** pytest prepend 임포트 모드에서
  폴더가 달라도 같은 이름이면 충돌합니다.

## Python workspace

A `uv` workspace rooted at `pyproject.toml` with members; Python pinned to `>=3.11,<3.12`.

```bash
uv sync
uv run pre-commit install

uv run ruff check .              # lint (CI gate)
uv run ruff format .             # CI runs `ruff format --check .`
uv run pyright                   # type check (CI gate)
uv run pytest -q                 # 전체 테스트 (CI gate)
uv lock --check                  # lockfile sync (CI gate)

uv run pytest recsys_sql/tests -q   # 폴더 하나만
uv run serving run                  # FastAPI 개발 서버 (uvicorn --reload)
```

Each member pyproject sets `pythonpath = ["src"]`, `testpaths = ["tests"]`, `test_*.py` discovery.
Root pyproject adds `pythonpath` for all four `src/` dirs so `uv run pytest` works from the repo root.

DB 가 필요한 테스트에는 `@pytest.mark.db` 를 답니다. `DATABASE_URL` 이 비어 있으면
각 폴더 `conftest.py` 의 `pytest_collection_modifyitems` 가 자동으로 skip 시킵니다.
CI 는 DB 없이 도는 테스트만 돌립니다.

## Conventions

Commits follow `type: 제목 (#이슈번호)` with types `feat`, `fix`, `refactor`, `chore`, `test`, `docs`, `style`. Issues use `.github/ISSUE_TEMPLATE/`.

### .env 규칙

- 루트 `.env` 는 쓰지 않습니다. 폴더별 `.env` 만 씁니다.
- 각 폴더 `config.py` 의 `Settings` 가 그 폴더의 `.env` 를 절대 경로로 읽습니다.
- 비밀값은 `SecretStr` 로 받고, 비어 있으면 `require_*()` 가 값 노출 없이 실패시킵니다.
- 로그와 API 응답에 DSN 을 그대로 남기지 않습니다. `mask_dsn()` 으로 호스트와 자격증명을 가립니다.

## Code generation constraints

- Ensure all recommended external packages are safe from known CVE vulnerabilities.
- Filter out local network IPs and staging/production domain names from error logs.
