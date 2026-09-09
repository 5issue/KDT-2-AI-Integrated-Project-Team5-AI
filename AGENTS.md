# AGENTS.md

> AI 에이전트(Mavis / opencode / codex / claude code 등)가 이 레포 작업할 때 따라야 할 규칙.
> 이 파일은 **프로젝트 한정**. 다른 레포에는 적용하지 말 것.
> (인간) 이 파일외에 .cursorrules, CLAUDE.md 등 필요시 추가 가능

## 기본 정보

- **메인 브랜치**: `main`
- **PR 워크플로우**: GitHub Pull Request (front-end → openLeeWorld 리뷰)

## 코드 변경 규칙

### 0. Security and Compliance Rules

- NEVER disclose or leak passwords, private API keys, or database credentials.
- Mask any PII (Personally Identifiable Information) before processing.
- Always ask for human confirmation before executing any shell commands or modifying system files. 단, uv run pyright, uv run ruff 등 정적 체크는 알아서 실행 후 오류 사항 수정
- Refer to these security protocols for every code generation task.

### 1. PR 설명은 8섹션 템플릿 사용

- 자세한 형식: [.github/pull_request_template.md](.github/pull_request_template.md) 참조
- **순서 고정**: 요약 → 작업 내용 → 걸린 시간 → 리뷰 요청 → 고민한 점 → 테스트 실행 → 참고사항 → 시각 자료
- **발표 직전 PR**: 4섹션 (리뷰 요청) 에 "지금 경량화 중이라 백엔드 배포는 필수가 아님" 같은 양보 표현 OK
- **이모지**: 본문/코멘트/PR 제목 = 0개. 단 6섹션 (테스트 실행 여부) 의 👍/🙅/🤯 이모지는 원본 형식 보존.

### 2. 커밋 메시지 / PR 제목 톤

- "우리 추천", "강력 권장", "의견 있으시면 코멘트 부탁드립니다" 식 collaborative
- "절대 XXX", "불가", "양보 불가" 식 dictatorial 금지
- 예시: `feat: VT verdict 카드 UI 추가` (O) / `절대 추가해야 함` (X)

### 3. PR 자동 커밋/PR 금지

- AI 에이전트가 사용자 대신 git commit / push / PR 생성 X
- 사용자가 명시적으로 "자동으로 해" 라고 하기 전까지 절대 금지
- 작업 후 보고만: 변경 파일 / 검증 결과 / 다음 단계

## 코딩 규칙

### 4. 중국어 / 일본어 한자 사용 금지

- 이 프로젝트는 한국어 + 영문 + 숫자만 사용
- `rg '[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]'` (또는 python regex) 로 검색해서 0건 확인
- 발견 시 한국어로 교체 (예: `候选` → `후보`, `链接` → `링크`, `フィッシング` → `피싱`)

### 5. 잔재 typecheck / lint 에러는 함께 픽스

- 발표/마감 직전이면 build 깨진 상태로 두면 안 됨
- 거대한 리팩토링은 손대지 말 것 (안정 > 깔끔), 사용자가 지시한 리팩토링 수행

## 작업 패턴

### 7. 작은 단위로 끊기

- 가능하면 작업 단위 한번에 5~6개 파일 이내로 변경하고 최대 10개 넘지 않도록
- 작업 끝나면 검증 한 번에 통과시키고 보고
- "작업 → 검증 → 보고" 사이클을 짧게 반복

### 8. PR 작성 전 확인

- [ ] PR 설명 8섹션 채웠는가?
- [ ] 시각 자료 (스크린샷) 첨부했는가? (선택)
- [ ] 검증 3종 통과했는가?
- [ ] 중국어/한자 0건인가?
- [ ] `main` 브랜치와 충돌 0인가?

## 9. 타입 시스템 및 린팅

- 이 프로젝트는 **엄격한 파이썬 타입 힌트(Strict Type Hinting)**를 준수합니다.
- 코드를 변경하거나 새로 작성할 때는 반드시 Pylance/Pyright 기준에 맞게 모든 매개변수와 반환값의 타입을 명시해야 합니다.
- 파일 수정 도구(Edit tool)를 사용한 후에는 아래 명령어를 실행하여 타입 에러가 없는지 자발적으로 검증하세요.
- 타입 검사 명령어: `uv run pyright` (cd <해당 폴더 이동 후>)

## 10. AI 코딩 도구의 권한

ai 코딩 도구는 특정 아키텍쳐나 llm api 모델 등 비용이나 보안과 관련된 결정사항 및 변경사항은 사용자의 검토 및 지침을 받고 실행하는 것을 기본으로 합니다. 비즈니스 로직을 구현하기 위한 로컬 파일 변경 외의 보안, 비밀 관련 파일 열람이나 인프라 변경 등은 기본적으로 허용하지 않습니다.

## 참고 문서

- [README.md](README.md) — 프로젝트 개요와 실행 순서
