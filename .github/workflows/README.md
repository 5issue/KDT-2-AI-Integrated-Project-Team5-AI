# GitHub Actions Workflows

CI/CD workflow 파일을 관리하는 디렉터리입니다.

## Workflows

- `ci.yaml`: Python lint/type/test 검증. 단위 테스트(DB 없음)는 항상 전부, DB 통합 테스트는 **바뀐 패키지와
  그것을 쓰는 쪽만** 돌립니다(`.github/scripts/select_db_tests.py`). 마이그레이션·루트 설정·CI 가 바뀌면 전부,
  Actions 에서 수동 실행(`workflow_dispatch`)해도 전부 돕니다.
- `cd-serving.yaml`: serving 이미지 ECR 빌드·배포 (#41)

## CI 가 쓰는 secret

| 이름                     | 값                               | 비고                                         |
| ------------------------ | -------------------------------- | -------------------------------------------- |
| `CI_DATABASE_URL`        | 테스트용 Neon 브랜치 pooler 주소 | DB 테스트 전체. 없으면 CI 가 멈춥니다(포크 PR 은 경고 후 DB 테스트만 건너뜀) |
| `CI_DATABASE_URL_DIRECT` | 같은 브랜치 직접(unpooled) 주소  | data_pipeline 의 직접 엔드포인트 확인 테스트 |

DB 테스트는 전부 롤백됩니다. 세션 끝에 루트 `conftest.py` 가 테스트 전용 ID 대역
(9,100M·9,300M)과 표식(`TEST-SEED`, `IT-SEED`, `PYTEST_*`)에 남은 행이 0인지 확인하고, 남았으면
CI 를 실패시킵니다. 9,200M 대역은 데모 시드라 보지 않습니다. 실제 LLM 을 부르는 `llm` 테스트는
CI 에서 돌지 않습니다(`--run-llm` 을 줄 때만).

- `security.yaml`: Gitleaks secret scan(깃허브 조직에서는 무료가 아니라 현재는 주석처리함), Trivy filesystem scan
- `codeql.yaml`: Python SAST
