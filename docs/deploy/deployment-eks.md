# serving EKS 배포 가이드

`serving/Dockerfile` 로 이미지를 만들어 ECR 에 올리고 EKS 에 배포하는 절차입니다.
환경변수 전달 방법과 밟기 쉬운 함정을 함께 적습니다.

기준 커밋의 `serving/src/serving/config.py` 와 `serving/Dockerfile` 을 실제로 읽고 쓴 문서입니다.
둘 중 하나가 바뀌면 이 문서도 같이 고쳐 주세요.

## 1. 한 이미지에 무엇이 들어가나

`serving` 과 그 워크스페이스 의존성인 `recsys_sql` 둘뿐입니다.
`rag_lab` 과 `data_pipeline` 은 들어가지 않습니다.

```
/app/.venv/          uv 가 락파일 그대로 설치한 가상환경 (non-root, 사용자 serving)
  serving/           FastAPI 앱
  recsys_sql/        SQL 카탈로그. queries/ 가 패키지 안에 있어 휠에 함께 담깁니다
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
(4-1 함정 참고). 컨테이너에는 `.env` 파일이 없으므로 환경변수가 유일한 입력입니다.

| 변수 | 기본값 | 어디에 | 설명 |
| --- | --- | --- | --- |
| `DATABASE_URL` | `None` | **Secret** | 실질적 필수. `SecretStr` 이라 로그·응답에 안 나옵니다 |
| `ENVIRONMENT` | 이미지에서 `prod` | ConfigMap | `local` 이 아니면 `/docs`·`/openapi.json` 이 닫힙니다 |
| `DB_POOL_MIN_SIZE` | `1` | ConfigMap | |
| `DB_POOL_MAX_SIZE` | `10` | ConfigMap | **파드당** 값 (4-4 참고) |
| `DB_POOL_TIMEOUT` | `10.0` | ConfigMap | 커넥션 대기 상한(초) |
| `DB_COMMAND_TIMEOUT` | `5.0` | ConfigMap | 쿼리 하나의 상한(초) |
| `CORS_ALLOW_ORIGINS` | `()` | ConfigMap | 쉼표 구분. 비우면 CORS 미들웨어를 안 켭니다 |
| `NEON_BRANCH` | `''` | ConfigMap(선택) | `/health/db` 응답 메모에만 씁니다 |

### 넣어도 소용없는 것 둘

| 변수 | 왜 |
| --- | --- |
| `HOST` | `Dockerfile` 의 `CMD` 가 `--host 0.0.0.0` 을 박아 둡니다 |
| `PORT` | 같은 이유로 `--port 8000` 고정입니다 |

둘은 `uv run serving run`(로컬 개발)만 읽습니다. 포트를 바꾸려면 `containerPort` 와
매니페스트의 `command` 를 함께 고쳐야 합니다.

### `recsys_sql` 용 변수는 필요 없습니다

같은 이미지에 들어가지만 서빙 경로에서는 자기 `.env` 를 요구하지 않습니다.
`repository` 가 카탈로그 경로를 직접 넘기고, 그쪽 `Settings` 도 전 필드에 기본값이 있습니다.

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
  CORS_ALLOW_ORIGINS: ""
  NEON_BRANCH: "dev/kipil"
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
  replicas: 2
  selector:
    matchLabels: { app: serving }
  template:
    metadata:
      labels: { app: serving }
    spec:
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

## 5. 밟기 쉬운 함정 다섯

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
최대 20 커넥션입니다. HPA 로 레플리카가 늘면 그만큼 곱해집니다. DB 쪽 한도와 대조하세요.

### 5-5. `sslmode` 가 접속 방식을 정합니다

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

## 8. 아직 안 된 것

배포 전에 알고 있어야 할 것들입니다.

- **`X-User-Id` 헤더를 검증 없이 믿습니다.** 서명도 공유 시크릿도 없어서, FastAPI 에 직접
  도달할 수 있으면 헤더 한 줄로 임의 사용자가 됩니다. **공유 시크릿이 붙기 전까지는 Service 를
  `ClusterIP` 로 두고 BFF 만 접근하게 하세요.** LoadBalancer/Ingress 로 외부에 노출하면 안 됩니다.
- **rate limit 이 없습니다.** 명세 에러표에 `429 TOO_MANY_REQUESTS` 가 계약으로 있는데 구현이
  없습니다. FE 가 429 처리를 짜 두었다면 영원히 오지 않습니다.
- **구조화 로그·요청 id·메트릭이 없습니다.** 500 이 나도 어느 요청이 왜 실패했는지 추적할 수
  없습니다. 파드 로그에 uvicorn 기본 출력만 남습니다.
- **보안 헤더가 없습니다**(HSTS, `X-Content-Type-Options` 등). BFF 뒤에 있으면 대개 BFF 가
  붙이지만, 직접 노출 경로가 생기면 필요합니다.
