# serving EKS 배포 가이드

`serving/Dockerfile` 로 이미지를 만들어 ECR 에 올리고 EKS 에 배포하는 절차입니다.
환경변수 전달 방법과 밟기 쉬운 함정을 함께 적습니다.

기준 커밋의 `serving/src/serving/config.py` 와 `serving/Dockerfile` 을 실제로 읽고 쓴 문서입니다.
둘 중 하나가 바뀌면 이 문서도 같이 고쳐 주세요.

## 1. 한 이미지에 무엇이 들어가나

`serving` 과 그 워크스페이스 의존성인 `recsys_sql`, `rag_lab` 셋입니다. `data_pipeline` 은
들어가지 않습니다. `rag_lab` 은 추천 이유 생성(`rag_lab.reason_service`)만 쓰며, 기본 의존성이
`httpx` 하나라 LangGraph·OpenAI SDK 같은 RAG 실험 패키지는 설치되지 않습니다(#33, #38).

```
/app/.venv/          uv 가 락파일 그대로 설치한 가상환경 (non-root, 사용자 serving)
  serving/           FastAPI 앱
  recsys_sql/        SQL 카탈로그. queries/ 가 패키지 안에 있어 휠에 함께 담깁니다
  rag_lab/           추천 이유 LLM 생성. 서빙은 reason_service 만 import 합니다
```

**빌드 컨텍스트는 레포 루트입니다.** `serving/` 이 아닙니다. 락파일을 풀려면 워크스페이스
멤버 4개의 `pyproject.toml` 이 전부 필요하기 때문입니다. 루트 `.dockerignore` 가
`.env`·키·가상환경·데이터를 걸러 줍니다(`serving/tests/test_build_context.py` 가 고정).

## 2. 이미지 빌드와 ECR 푸시

```bash
ACCOUNT=<계정ID>
REGION=ap-northeast-2
REPO=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/team5-serving
TAG=$(git rev-parse --short HEAD)

aws ecr create-repository --repository-name team5-serving --region $REGION   # 최초 1회

aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# 레포 루트에서 실행합니다. 마지막 `.` 이 빌드 컨텍스트입니다.
docker build --platform linux/amd64 -f serving/Dockerfile -t $REPO:$TAG .
docker push $REPO:$TAG
```

- **`latest` 태그를 쓰지 마세요.** 무엇이 떠 있는지 알 수 없고 롤백이 안 됩니다.
- **`--platform linux/amd64` 를 빼지 마세요.** Apple Silicon 에서 만들면 arm64 이미지가 나와
  x86 노드에서 뜨지 않습니다. Graviton 노드를 쓴다면 반대로 맞추면 됩니다.
- **빌드 인자로 비밀값을 넘기지 마세요.** `ARG`/`ENV` 로 넣은 값은 레이어에 남아
  `docker history` 로 보입니다.

로컬에서 먼저 확인하려면:

```bash
docker run --rm -p 8000:8000 -e DATABASE_URL="postgresql://..." $REPO:$TAG
curl -s localhost:8000/health && curl -s localhost:8000/health/db
```

## 3. 환경변수 목록

`Settings` 는 **필수 항목이 하나도 없습니다.** 전부 기본값이 있어 아무것도 안 넘겨도 앱이 뜹니다
(5-1 함정 참고). 컨테이너에는 `.env` 파일이 없으므로 환경변수가 유일한 입력입니다.

### 3-1. 클라우드팀 전달용 요약

**Secret 2개** (값은 AI 파트가 전달합니다. 매니페스트에 평문으로 넣지 마세요)

| 키 | 값 형태 | 누가 채우나 | 비고 |
| --- | --- | --- | --- |
| `DATABASE_URL` | `postgresql://USER:PASS@HOST/db?sslmode=...` | **클라우드팀** | 운영 DB(CNPG)의 DSN. AI 팀의 Neon 개발 브랜치를 그대로 쓰지 않습니다 (3-6) |
| `OPENROUTER_API_KEY` | `sk-or-...` | AI 팀 | 추천 이유 LLM 생성용. 없으면 규칙 문구로만 뜹니다 (3-3) |

**ConfigMap** (그대로 복사해서 값만 확인하시면 됩니다)

```
ENVIRONMENT=prod
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=10
DB_POOL_TIMEOUT=10
DB_COMMAND_TIMEOUT=5
SHUTDOWN_DELAY_SECONDS=5
RATE_LIMIT_PER_MINUTE=60
RATE_LIMIT_RECO_PER_MINUTE=10
CORS_ALLOW_ORIGINS=
FORWARDED_ALLOW_IPS=*
# NEON_BRANCH 는 Neon 을 쓸 때만. CNPG 면 넣지 않습니다 (3-6)
NEON_BRANCH=
# 비우면 코드 기본값(google/gemini-3.5-flash-lite)을 씁니다 (3-3)
REASON_MODEL=google/gemini-3.5-flash-lite
```

**환경별로 다른 값은 셋뿐입니다.**

| 키 | local | dev | prod |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `local` | `dev` | `prod` |
| `SHUTDOWN_DELAY_SECONDS` | `0` | `5` | `5` |
| `CORS_ALLOW_ORIGINS` | 비움 | FE dev 도메인 | 비움(BFF 경유) |

**클러스터 설정 쪽에서 함께 맞춰야 하는 것**

| 항목 | 값 | 왜 |
| --- | --- | --- |
| `Service.type` | `ClusterIP` | 인증이 `X-User-Id` 헤더 방식(private network 전제)입니다 |
| `terminationGracePeriodSeconds` | `30` | `SHUTDOWN_DELAY_SECONDS` + 처리 중 요청보다 커야 합니다 (5-6) |
| `readinessProbe` | `/health/db` | `/health` 로 두면 DB 미연결을 못 잡습니다 (5-1) |
| `livenessProbe` | `/health` | DB 상태로 컨테이너를 재시작시키지 않기 위해 |
| `replicas` | 우선 `1` | 늘리면 rate limit 실효 한도가 배수가 됩니다 (5-4) |

### 3-2. 전체 목록과 설명

| 변수 | 기본값 | 어디에 | 설명 |
| --- | --- | --- | --- |
| `DATABASE_URL` | `None` | **Secret** | 실질적 필수. `SecretStr` 이라 로그·응답에 안 나옵니다 |
| `ENVIRONMENT` | 이미지에서 `prod` | ConfigMap | **`prod` 에서만** `/docs`·`/openapi.json` 이 닫힙니다 |
| `DB_POOL_MIN_SIZE` | `1` | ConfigMap | |
| `DB_POOL_MAX_SIZE` | `10` | ConfigMap | **파드당** 값 (5-4 참고) |
| `DB_POOL_TIMEOUT` | `10.0` | ConfigMap | 커넥션 대기 상한(초) |
| `DB_COMMAND_TIMEOUT` | `5.0` | ConfigMap | 쿼리 하나의 상한(초) |
| `SHUTDOWN_DELAY_SECONDS` | `5.0` | ConfigMap | SIGTERM 후 대기(초). EKS 는 5, 로컬·테스트는 0 (5-6) |
| `RATE_LIMIT_PER_MINUTE` | `60` | ConfigMap | `/api/v1` 분당 한도. 0 이면 비활성 |
| `RATE_LIMIT_RECO_PER_MINUTE` | `10` | ConfigMap | 추천 경로 전용(더 낮은) 한도. 0 이면 기본 한도를 따름 |
| `CORS_ALLOW_ORIGINS` | `()` | ConfigMap | 쉼표 구분. 비우면 CORS 미들웨어를 안 켭니다 |
| `NEON_BRANCH` | `''` | ConfigMap(선택) | **Neon 전용.** `/health/db` 응답 메모에만 씁니다. CNPG 면 비웁니다 (3-6) |
| `FORWARDED_ALLOW_IPS` | uvicorn 기본 `127.0.0.1` | ConfigMap | **LB 뒤에서는 반드시 설정** (5-5) |

`FORWARDED_ALLOW_IPS` 만 `Settings` 필드가 아니라 uvicorn 이 직접 읽는 값입니다.

### 3-3. 추천 이유 LLM 변수

`my-recipes` 의 `recommendation_reason` 은 앞 카드 3장만 OpenRouter 로 만들고 나머지는 규칙
문구입니다(#38). 필요한 변수는 **둘뿐**이고, 둘 다 `serving` 의 `Settings` 가 읽어
`rag_lab.reason_service` 에 넘깁니다.

| 변수 | 기본값 | 어디에 | 설명 |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | `None` | **Secret** | 비어 있으면 LLM 을 부르지 않고 전부 규칙 문구입니다. 서비스는 정상입니다 |
| `REASON_MODEL` | `google/gemini-3.5-flash-lite` | ConfigMap | OpenRouter 모델명(`<vendor>/<model>`). 비우면 기본값 |

**환경변수가 아닌 것:** 카드별 제한 시간(2초)과 LLM 카드 수(3장)는 `reason_service/service.py`
의 상수입니다. 설정으로 풀 수 없게 일부러 막아 두었습니다.

**넣지 않는 것:** `LLM_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL` 과 `LLM_EMBEDDING_*`,
`JUDGE_*` 등은 `rag_lab` 의 **RAG 실험용** `Settings` 가 읽는 이름입니다. 서빙 경로는 그 설정을
import 하지 않으므로 넣어도 아무 효과가 없습니다. 특히 `LLM_API_KEY` 에 키를 넣으면 LLM 이
켜진 줄 알지만 실제로는 규칙 문구만 나갑니다.

**켜졌는지는 기동 로그로 확인합니다.**

```
INFO serving: 추천 이유 LLM 생성 사용: model=google/gemini-3.5-flash-lite, 사전 955종   <- 켜짐
WARNING serving: 추천 이유는 규칙 기반 문구만 씁니다: OPENROUTER_API_KEY 가 비어 있습니다.  <- 꺼짐
```

키가 틀려도 파드는 정상으로 뜨고 `my-recipes` 도 200 입니다. 카드마다 규칙 문구로 대체될 뿐이라
응답만 봐서는 모릅니다. 요청 로그에서 대체 건수를 보세요(7절).

```
INFO rag_lab.reason_service.service: 추천 이유 생성 실패, 템플릿으로 대체: ReasonClientError
INFO rag_lab.reason_service.service: 추천 이유 생성: 카드 3장, LLM 0장, 대체 3장
```

### 3-4. `ENVIRONMENT` 값에 따라 스웨거가 열립니다

| 값 | `/docs`, `/openapi.json` |
| --- | --- |
| `local` | 열림 |
| `dev` | **열림** - FE 연동 전 계약 확인용 |
| `prod` | 닫힘 |

`dev` 클러스터를 외부에 노출한다면 스키마가 그대로 공개된다는 뜻입니다. 인그레스에 basic auth 를
걸거나 사내망으로 제한하세요.

### 3-5. 넣어도 소용없는 것 둘

| 변수 | 왜 |
| --- | --- |
| `HOST` | `Dockerfile` 의 `CMD` 가 `--host 0.0.0.0` 을 박아 둡니다 |
| `PORT` | 같은 이유로 `--port 8000` 고정입니다 |

둘은 `uv run serving run`(로컬 개발)만 읽습니다. 포트를 바꾸려면 `containerPort` 와
매니페스트의 `command` 를 함께 고쳐야 합니다.

`recsys_sql` 은 같은 이미지에 들어가지만 자기 `.env` 를 요구하지 않습니다.
`repository` 가 카탈로그 경로를 직접 넘기고, 그쪽 `Settings` 도 전 필드에 기본값이 있습니다.

### 3-6. 운영 DB: Neon vs CloudNativePG

**`DATABASE_URL` 은 클라우드팀이 채웁니다.** AI 팀이 쓰는 값은 Neon 의 개발 브랜치
(`dev/kipil`)이고, 운영에 그대로 넣을 값이 아닙니다.

코드는 둘 다 지원합니다. `db.normalize_neon_dsn()` 이 DSN 을 보고 알아서 맞춥니다
(이름만 Neon 이고 동작은 일반 PostgreSQL 기준입니다).

| | Neon | CloudNativePG (클러스터 내부) |
| --- | --- | --- |
| 위치 | 외부 관리형 | EKS 안 |
| 지연 | 리전 왕복 (수십 ms) | 파드 간 (수 ms) |
| TLS | `sslmode=require` 필수 | `sslmode=disable` 가능 (파드 간 구간) |
| 커넥션 | 한도가 빡빡, **pooler 사실상 필수** | 넉넉. PgBouncer 는 선택 |
| statement 캐시 | pooler 면 자동으로 꺼짐 (`-pooler.` 감지) | PgBouncer 를 안 쓰면 켠 채로 |
| pgvector | 기본 제공 | **이미지에 확장이 있어야 함** |
| 백업·HA·업그레이드 | Neon 이 함 | **클라우드팀이 함** |
| 브랜치 | 있음 (`dev/kipil`) | 없음 -> `NEON_BRANCH` 가 무의미 |
| 비용 | 사용량 과금 | 노드·스토리지에 포함 |

**환경변수에 미치는 영향은 둘뿐입니다.**

| 변수 | Neon | CNPG |
| --- | --- | --- |
| `DATABASE_URL` | `...?sslmode=require`, 호스트에 `-pooler.` | `postgresql://user:pw@cluster-rw.ns.svc:5432/db?sslmode=disable` |
| `NEON_BRANCH` | `dev/kipil` 등 | **비웁니다** (`/health/db` 메모에만 쓰는 값) |

나머지 변수는 그대로입니다. `DB_POOL_MAX_SIZE` 는 CNPG 쪽이 여유로워 올릴 수 있지만,
`max_connections` 를 먼저 확인하고 정하세요.

#### CNPG 로 갈 때 클라우드팀에 필요한 것

- [ ] **pgvector 확장** - `recipe`/`product`/`ingredient` 의 `embedding` 이 `VECTOR(1536)` 입니다.
      CNPG 기본 이미지에는 없을 수 있어 확장이 포함된 이미지가 필요하고,
      DB 생성 후 `CREATE EXTENSION vector;` 가 한 번 필요합니다.
- [ ] **`max_connections`** - 파드 수 x `DB_POOL_MAX_SIZE` 를 감당할 값 (5-4)
- [ ] **백업/복구 정책** - Neon 이 해 주던 몫입니다
- [ ] 애플리케이션 전용 롤 (superuser 아님)

#### 스키마·데이터는 pg_dump 로 통째로 넘깁니다

alembic 으로는 빈 DB 를 못 채웁니다. `0001_baseline.py` 가 **아무것도 하지 않는 stamp**
(*"이 저장소에는 현재 production 스키마를 만든 DDL 이 없다"*)이고, `0002`~`0012` 는 그
테이블들이 있다고 전제하고 `ALTER` 를 걸기 때문입니다. 빈 DB 에 `upgrade head` 를 걸면
0002 부터 깨집니다.

**baseline 마이그레이션을 새로 쓰는 대신 덤프를 넘기는 쪽이 빠릅니다.**

```bash
# AI 팀: Neon 에서 통째로 뽑아 전달 (pg_dump 가 CREATE EXTENSION vector 까지 담습니다)
pg_dump "$DATABASE_URL" -Fc -f team5-ai.dump        # 압축 포맷 권장 (임베딩이 커서)

# 클라우드팀: CNPG 에 복원
pg_restore -d "$CNPG_URL" --no-owner --no-privileges team5-ai.dump
```

- **`-Fc` 를 쓰세요.** `VECTOR(1536)` 5,800여 행이 평문 SQL 이면 100MB 대가 됩니다.
- 복원 뒤 **`alembic stamp head`** 를 한 번 걸어야 이후 마이그레이션이 이어집니다.
- `pg_dump` 는 서버 버전 이상이어야 합니다. 이 레포에는 설치돼 있지 않으니
  `brew install libpq` 또는 postgres 컨테이너로 뽑으세요.
- 소유자·권한은 환경이 다르므로 `--no-owner --no-privileges` 로 털어냅니다.

## 4. 매니페스트

### 4-1. Secret 과 ConfigMap

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: serving-secret
type: Opaque
stringData:
  DATABASE_URL: "postgresql://USER:PASS@HOST/db?sslmode=require"
  # 추천 이유 LLM 생성 (3-3). 빼 두면 규칙 문구로만 뜹니다.
  OPENROUTER_API_KEY: "sk-or-..."
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: serving-config
data:
  ENVIRONMENT: "prod"
  DB_POOL_MIN_SIZE: "1"
  DB_POOL_MAX_SIZE: "10"
  DB_POOL_TIMEOUT: "10"
  DB_COMMAND_TIMEOUT: "5"
  SHUTDOWN_DELAY_SECONDS: "5"
  RATE_LIMIT_PER_MINUTE: "60"
  RATE_LIMIT_RECO_PER_MINUTE: "10"
  CORS_ALLOW_ORIGINS: ""
  NEON_BRANCH: "dev/kipil"
  # LB/인그레스를 거치면 반드시 채웁니다 (5-5). 파드 CIDR 또는 "*"
  FORWARDED_ALLOW_IPS: "*"
  # 추천 이유 LLM 모델 (3-3). 비우면 코드 기본값입니다.
  REASON_MODEL: "google/gemini-3.5-flash-lite"
```

**ConfigMap 값은 전부 따옴표로 감싸 문자열로 두세요.** `DB_POOL_MAX_SIZE: 10` 처럼 쓰면
YAML 이 정수로 파싱하고 kubectl 이 거부합니다.

평문 DSN 이 든 Secret 은 git 에 올리면 안 됩니다. 프로덕션은 6절을 보세요.

### 4-2. Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: serving
spec:
  # rate limit 이 프로세스 메모리라 늘리면 실효 한도가 배수가 됩니다 (5-4).
  replicas: 1
  selector:
    matchLabels: { app: serving }
  template:
    metadata:
      labels: { app: serving }
    spec:
      # SHUTDOWN_DELAY_SECONDS(5) + 처리 중 요청 시간보다 넉넉해야 합니다 (5-6).
      terminationGracePeriodSeconds: 30
      containers:
        - name: serving
          image: <ECR>/team5-serving:<커밋해시>
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef: { name: serving-config }
            - secretRef:    { name: serving-secret }
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 30
          readinessProbe:
            httpGet: { path: /health/db, port: 8000 }
            initialDelaySeconds: 3
            periodSeconds: 10
          resources:
            requests: { cpu: "100m", memory: "256Mi" }
            limits:   { memory: "512Mi" }
---
apiVersion: v1
kind: Service
metadata:
  name: serving
spec:
  type: ClusterIP          # 7절 참고. 외부 노출은 아직 이릅니다
  selector: { app: serving }
  ports:
    - port: 80
      targetPort: 8000
```

## 5. 밟기 쉬운 함정

### 5-1. `DATABASE_URL` 이 없어도 파드가 정상으로 뜹니다

`app.py` 는 DSN 이 없으면 `"DB 없이 기동합니다"` 경고만 찍고 계속 갑니다. `/health` 는 DB 를
건드리지 않으므로 liveness 를 통과하고, 파드는 `Running` 인 채 **모든 요청에 503** 을 냅니다.

-> **readinessProbe 를 반드시 `/health/db` 로 두세요.** 그래야 Secret 오타가 트래픽을 받기 전에
걸립니다. `Dockerfile` 의 `HEALTHCHECK` 는 쿠버네티스에서 무시되니 의존하지 마세요.

### 5-2. 변수 이름 오타가 조용히 무시됩니다

`Settings` 가 `extra="ignore"` 라 `DATABSE_URL` 처럼 잘못 써도 에러 없이 기본값으로 갑니다.
5-1 과 겹치면 "파드는 멀쩡한데 전부 503" 이 되어 원인 찾기가 오래 걸립니다.
대소문자는 상관없습니다(`case_sensitive=False`).

### 5-3. Secret 을 바꿔도 파드가 다시 읽지 않습니다

`get_settings()` 가 `@lru_cache(maxsize=1)` 라 프로세스당 한 번만 읽습니다. 게다가 `envFrom`
방식은 볼륨 마운트와 달리 **재시작 없이 갱신되지 않습니다.**

```bash
kubectl rollout restart deployment/serving
```

### 5-4. 총 커넥션 = 레플리카 수 x `DB_POOL_MAX_SIZE`

`CMD` 에 `--workers` 가 없어 파드당 1 프로세스입니다. 레플리카 2 + `DB_POOL_MAX_SIZE=10` 이면
최대 20 커넥션입니다(그래서 매니페스트 예시는 `replicas: 1` 입니다). HPA 로 레플리카가 늘면 그만큼 곱해집니다. DB 쪽 한도와 대조하세요.

**같은 배수가 rate limit 에도 걸립니다.** `ratelimit.py` 는 프로세스 메모리 카운터라
레플리카마다 따로 셉니다. `replicas: 2` + `RATE_LIMIT_PER_MINUTE=60` 이면 실효 한도가
**분당 120** 입니다. 게다가 어느 파드로 갈지는 LB 가 정하므로 한 사용자의 한도가
일정하지 않습니다. 정확한 한도가 필요해지면 Redis 백엔드로 바꿔야 합니다.

### 5-5. LB 뒤에서는 rate limit 이 전원 공용 한 통이 됩니다

`ratelimit.py` 의 식별 키는 **`X-User-Id` 가 있으면 사용자, 없으면 클라이언트 IP** 입니다.

```python
key = request.headers.get("X-User-Id") or (request.client.host if request.client else "unknown")
```

문제는 ALB/인그레스를 거치면 `request.client.host` 가 **원 클라이언트가 아니라 LB 의 IP** 라는
점입니다. uvicorn 은 `proxy_headers=True` 가 기본이지만 `forwarded_allow_ips` 기본값이
`127.0.0.1` 이라, 바로 앞 홉이 LB 면 `X-Forwarded-For` 를 **신뢰하지 않고 버립니다.**

그래서 비로그인 요청 전부가 키 하나("LB IP")를 공유합니다. `RATE_LIMIT_PER_MINUTE=60` 이면
**전 사용자 합쳐 분당 60건** 이 되어, 조금만 몰려도 서로가 서로를 429 로 막습니다.

-> `FORWARDED_ALLOW_IPS` 를 설정하세요. 값이 채워지면 uvicorn 이 `X-Forwarded-For` 를 반영해
`request.client.host` 를 원 클라이언트로 바꿔 줍니다.

```yaml
FORWARDED_ALLOW_IPS: "*"        # 신뢰 경계가 LB 하나로 확실할 때
# 또는 파드 CIDR 등 실제 프록시 대역만
```

**`"*"` 는 "앞단 프록시가 헤더를 덮어쓴다" 는 전제에서만 안전합니다.** 파드에 직접 도달할 수
있는 경로가 있으면 클라이언트가 `X-Forwarded-For` 를 위조해 한도를 우회합니다. Service 를
`ClusterIP` 로 두라는 8절 권고와 같은 이유입니다.

참고로 액세스 로그(`request_log.py`)는 `X-Forwarded-For` 를 직접 읽습니다. 다만 그건
**추적용이고 위조 가능한 값이라고 명시**되어 있어, 판단에 쓰는 rate limit 과는 성격이 다릅니다.

### 5-6. 종료 유예는 두 군데를 맞춰야 합니다

SIGTERM 을 받으면 uvicorn 이 처리 중 요청을 끝낸 뒤 lifespan 의 `finally` 로 들어가고,
거기서 `SHUTDOWN_DELAY_SECONDS` 만큼 기다렸다가 커넥션 풀을 닫습니다. **ALB 의 타깃 제외가
전파되기 전에 풀을 닫으면 그 사이 들어온 요청이 502 로 끊기기 때문**입니다.

`terminationGracePeriodSeconds` 가 이 지연보다 짧으면 쿠버네티스가 SIGKILL 을 보내
정리가 중간에 끊깁니다.

```
terminationGracePeriodSeconds  >  SHUTDOWN_DELAY_SECONDS + 처리 중 요청 최대 시간
                       30초   >  5초 + DB_COMMAND_TIMEOUT(5초)
```

로컬과 테스트에서는 `SHUTDOWN_DELAY_SECONDS=0` 으로 두세요. 안 그러면 `Ctrl+C` 마다 5초씩
기다립니다. 로컬 `docker stop` 도 같습니다. 2026-09-28 Docker Desktop(29.8)에서 기본값으로 멈추면
약 3초 만에 강제 종료(exit 137)됐고, `docker stop -t 20` 이면 5.4초에 정상 종료(exit 0)했습니다.

정상 종료는 로그 세 줄로 확인합니다. 이 줄이 없으면 강제 종료된 것입니다.

```
graceful shutdown 시작 - 처리 중 요청 완료됨, 리소스 정리
커넥션 풀 정리 완료
graceful shutdown 완료
```

### 5-7. `sslmode` 가 접속 방식을 정합니다

DSN 의 `sslmode` 를 보고 asyncpg 연결 옵션을 정합니다(`db.normalize_neon_dsn`).

| DSN 의 `sslmode` | 동작 |
| --- | --- |
| 없음 / `require` | `ssl="require"` |
| `verify-full` | `ssl="verify-full"` |
| `disable` / `allow` | `ssl=False`. 클러스터 내부 DB(CloudNativePG 등) 용 |

호스트에 `-pooler.` 가 들어 있으면 statement 캐시를 자동으로 끕니다. PgBouncer transaction
모드에서는 prepared statement 를 재사용할 수 없기 때문입니다. Neon 을 쓴다면 pooler 호스트인지
확인하세요.

## 6. 프로덕션: Secret 을 git 에 올리지 않기

External Secrets Operator + AWS Secrets Manager 조합이면 매니페스트에 값이 없어 그대로
커밋할 수 있습니다.

`apiVersion` 은 클러스터에 깔린 ESO 버전을 따릅니다. 먼저 확인하세요.

```bash
kubectl api-resources | grep externalsecrets
```

```yaml
apiVersion: external-secrets.io/v1beta1   # 위 명령이 알려 주는 버전으로 맞추세요
kind: ExternalSecret
metadata:
  name: serving-secret
spec:
  secretStoreRef: { name: aws-secretsmanager, kind: ClusterSecretStore }
  target: { name: serving-secret }
  data:
    - secretKey: DATABASE_URL
      remoteRef: { key: prod/serving, property: DATABASE_URL }
    - secretKey: OPENROUTER_API_KEY
      remoteRef: { key: prod/serving, property: OPENROUTER_API_KEY }
```

5-3 의 재시작 문제는 Reloader 같은 컨트롤러로 자동화할 수 있습니다.

## 7. 배포 후 확인

```bash
kubectl rollout status deployment/serving
kubectl get pods -l app=serving

kubectl port-forward svc/serving 8080:80
curl -s localhost:8080/health      # {"status":"ok","version":"0.1.0","environment":"prod"}
curl -s localhost:8080/health/db   # ok:true, latency_ms, pool_size ...
```

**`/health` 의 `version` 으로는 어떤 이미지가 떠 있는지 알 수 없습니다.**
`serving/__init__.py` 의 `__version__` 이 `"0.1.0"` 으로 고정이라 태그와 무관하게 같은 값이
나옵니다. 실제로 떠 있는 것은 이렇게 확인하세요.

```bash
kubectl get deployment serving -o jsonpath='{.spec.template.spec.containers[0].image}'
```

`/health/db` 가 `ok:false` 면 `detail` 에 예외 타입만 있습니다. 호스트나 자격증명은
일부러 싣지 않으므로, 원인은 파드 로그를 보세요.

```bash
kubectl logs -l app=serving --tail=50
```

### 요청 추적

모든 `/api/v1` 응답에 `X-Request-Id` 가 실리고, 요청마다 JSON 한 줄이 남습니다.
BFF 가 같은 헤더를 보내면 그 값을 그대로 이어받아 경계 간 추적이 이어집니다
(형식 검증을 통과한 값만 신뢰합니다 - 로그 인젝션 방지).

```bash
curl -si localhost:8080/api/v1/home/bubbles | grep -i x-request-id
kubectl logs -l app=serving | grep '"request_id":"<값>"'
```

헬스체크(`/health`, `/health/db`)는 로그를 남기지 않습니다. 프로브가 30초마다 찍는 노이즈를
막기 위한 것이라, 프로브 실패는 로그가 아니라 `kubectl describe pod` 로 봅니다.
같은 이유로 `Dockerfile` 의 `CMD` 가 uvicorn 의 평문 액세스 로그를 끕니다(`--no-access-log`).
액세스 로그는 앱의 JSON 한 줄뿐입니다.

### 추천 이유 LLM 확인

```bash
kubectl logs -l app=serving | grep "추천 이유"
```

기동 때 `추천 이유 LLM 생성 사용` 이 있어야 하고, `my-recipes` 요청마다
`카드 3장, LLM n장, 대체 m장` 이 남습니다. `LLM 0장` 이 계속되면 키나 모델명을 의심하세요(3-3).

### rate limit 동작 확인

```bash
for i in $(seq 1 70); do curl -s -o /dev/null -w "%{http_code} " localhost:8080/api/v1/home/bubbles; done
```

기본 한도(60/분)를 넘으면 `429` 와 `Retry-After` 헤더가 나옵니다. 추천 경로
(`/api/v1/recommendations/...`)는 별도 한도(10/분)입니다.

## 8. 아직 안 된 것

배포 전에 알고 있어야 할 것들입니다.

- **인증은 `X-User-Id` 헤더 방식으로 확정됐습니다** (2026-09-22 FE 협의, Bearer 전환 보류).
  **private network 전제**이므로 Service 를 `ClusterIP` 로 두고 BFF 만 접근하게 해야 합니다.
  LoadBalancer/Ingress 로 외부에 노출하면 전제가 깨집니다 - 헤더 한 줄로 임의 사용자가 되고,
  같은 헤더가 rate limit 키라 한도도 함께 우회됩니다.
- **메트릭 엔드포인트가 없습니다.** 요청 id 와 액세스 로그는 들어왔지만(#25) `/metrics` 는
  아직입니다. 지연·에러율 집계는 로그를 긁어야 합니다.
- **rate limit 이 프로세스 메모리입니다.** 단일 컨테이너 전제라 레플리카를 늘리면 실효 한도가
  배수가 됩니다(5-4). 정확한 한도가 필요하면 Redis 백엔드가 필요합니다.
- **보안 헤더가 없습니다**(HSTS, `X-Content-Type-Options` 등). BFF 뒤에 있으면 대개 BFF 가
  붙이지만, 직접 노출 경로가 생기면 필요합니다.
- **CORS 가 `GET` 만 허용합니다.** My냉장고 쓰기 3종(POST/PATCH/DELETE)이 브라우저에서 직접
  호출되면 preflight 에서 막힙니다. BFF 가 서버에서 부르는 지금 구조에서는 드러나지 않습니다.

### 이번에 들어온 것 (참고)

| PR | 내용 | 관련 절 |
| --- | --- | --- |
| #22 | DSN `sslmode` 로 ssl 옵션 결정 (클러스터 내부 DB 대응) | 5-7 |
| #24 | `/api/v1` rate limit (429 + `Retry-After`) | 3, 5-4, 5-5 |
| #25 | 요청 id + 구조화 액세스 로그, graceful shutdown | 5-6, 7 |
| #29 | `dev` 에서 스웨거 개방 | 3 |
| #38 | `my-recipes` 추천 이유 LLM 생성 (`OPENROUTER_API_KEY`, `REASON_MODEL`) | 1, 3-3, 7 |
| - | 앱 로그 출력 설정. 이전에는 INFO 로그(액세스 로그 포함)가 컨테이너에서 나가지 않았습니다 | 5-6, 7 |
