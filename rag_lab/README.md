# rag_lab

LangGraph + pgvector 기반 RAG 실험 환경입니다.

핵심은 **같은 질문 세트를 파라미터만 바꿔 반복해서 돌리고 결과를 비교**하는 것입니다.
그래서 검색 파라미터를 전부 `.env` 로 빼고, 실행 결과에 그때의 파라미터를 같이 기록합니다.

## 최종 목표: 여기도 serving 의 repository layer 입니다

`recsys_sql` 은 이미 그렇게 정리했습니다 — `serving` 이 `.sql` 복사본을 갖지 않고
`recsys_sql` 카탈로그를 직접 import 합니다. 폴더마다 다른 것은 `.env` 뿐입니다.

`rag_lab` 도 같은 모양이어야 합니다. 실험이 끝난 검색·생성 경로를 서빙이 그대로
불러 쓸 수 있어야 하고, 코드를 옮겨 붙이면 안 됩니다. 참고할 구조는
`recsys_sql/src/recsys_sql/repository.py` 입니다.

지금 상태와 그때의 차이는 이렇습니다.

| | 지금 | 필요한 것 |
| --- | --- | --- |
| 진입점 | `experiments/<owner>/` 스크립트 + CLI | 서빙이 부를 수 있는 함수 하나 |
| 의존성 주입 | 실험이 직접 조립 | 서빙이 자기 풀·클라이언트를 넘길 수 있게 |
| DB 연결 | `rag_lab/.env` | 호출자가 넘긴 것을 쓰도록 |
| 검색 SQL | `retrieval.py` 안 | 그대로 두어도 되고, 카탈로그로 빼도 됨 |

의존성을 전부 밖에서 주입받는 구조는 이미 되어 있으니(`rag_lab.testing` 참고),
남은 것은 서빙이 부를 공개 함수를 정하고 `__init__.py` 로 내보내는 정도입니다.

## 시작하기

```bash
cp rag_lab/.env.example rag_lab/.env   # 본인 Neon 브랜치 URL 과 OpenAI 키
uv sync --all-packages --all-groups
uv run rag-lab check-db                # pgvector 설치 여부까지 확인
uv run rag-lab route "대파 어떻게 보관해"   # DB/API 없이 라우팅만 확인
uv run rag-lab ask "김치로 뭐 해먹지"
```

## 그래프 구조

```
question ─▶ route ─▶ embed ─▶ retrieve ─▶ generate ─▶ answer
              │
              └─ recipe / product / ingredient 중 어디를 뒤질지 결정
```

라우팅을 노드로 분리한 이유는, 같은 질문이라도 "뭐 해먹지"는 `recipe` 를,
"이거 어디서 사"는 `product` 를 봐야 하기 때문입니다. 라우터가 규칙 기반이라
결과가 재현되고, 실험에서 **라우팅 품질과 검색 품질을 따로** 볼 수 있습니다.
LLM 라우터로 바꾸고 싶으면 `graph.route_question` 만 갈아끼우면 됩니다.

의존성(임베딩/LLM/DB 커넥션)은 전부 밖에서 주입받습니다. `rag_lab.testing` 의 가짜
클라이언트를 끼우면 API 키 없이 그래프 전체가 돕니다. 테스트도 그렇게 돌고 있습니다.

## 설계에서 지킨 것

- **근거가 없으면 LLM 을 부르지 않습니다.** 환각과 불필요한 비용을 같이 막습니다.
- **근거는 `<context>` 로 감쌉니다.** 검색된 문서 안에 지시문이 들어 있어도 따르지 않도록
  시스템 프롬프트에 못박았습니다(OWASP LLM01). DB 안의 텍스트도 신뢰 대상이 아닙니다.
- **벡터는 바인딩으로 넘어갑니다.** `CAST(:query_vector AS vector)` 형태라 값이 SQL 문자열에
  끼어들지 않습니다. 연산자와 테이블명은 화이트리스트에서만 나옵니다.
- **자격증명은 실험 기록에 남지 않습니다.** `snapshot_params` 가 검색 파라미터만 뽑습니다.

## 3명이 나눠 쓰는 방법

```
experiments/
├── _template/          질문 세트 예시 + 결과 폴더
├── <github_id>/        각자 폴더
└── ...
```

본인 github id 로 폴더를 만들고 `.env` 의 `EXPERIMENT_OWNER` 에 같은 값을 넣으면
결과가 `experiments/<owner>/results/` 에 쌓입니다. 결과 파일이 각자 폴더로 갈려서
세 명이 같은 파일을 건드릴 일이 없습니다.

DB 도 각자 Neon 브랜치를 씁니다. 인덱스를 만들거나 지우는 실험이 잦은데,
main 브랜치에 직접 붙으면 다른 사람 실험이 같이 흔들립니다.

## 실험 돌리기

```bash
uv run rag-lab experiment --name topk5 --cases rag_lab/experiments/_template/questions.jsonl
```

`.env` 의 `TOP_K`, `SCORE_THRESHOLD`, `DISTANCE_METRIC` 을 바꿔가며 같은 세트를 돌립니다.
결과 JSONL 첫 줄에 그때의 파라미터가 남고, 리포트에는 라우팅 정확도 / 검색 적중률 /
평균 지연이 찍힙니다.

질문 세트에 `expected_route` 와 `expected_doc_ids` 를 달아 두면 그 케이스만 채점에 들어갑니다.
정답을 모르는 질문은 기대값 없이 넣어도 됩니다. 지연 시간 비교에는 그대로 쓰입니다.

## 검색 파라미터

| 값 | 의미 |
| --- | --- |
| `TOP_K` | 벡터 검색으로 가져올 후보 수 |
| `SCORE_THRESHOLD` | 이 점수 아래는 근거로 쓰지 않음 (코사인 기준 0~1) |
| `DISTANCE_METRIC` | `cosine` / `l2` / `inner_product` |

거리는 지표마다 범위가 달라서 `retrieval.distance_to_score` 가 0~1 에 가깝게 맞춰 줍니다.
cosine 은 `1 - 거리`, l2 는 `1 / (1 + 거리)`, inner_product 는 부호만 뒤집습니다.
지표를 바꾸면 `SCORE_THRESHOLD` 의 의미도 달라지니 같이 조정하세요.

## 테스트

```bash
uv run pytest rag_lab/tests -q
```

| 파일 | DB/API 필요 | 하는 일 |
| --- | --- | --- |
| `tests/test_retrieval.py` | 아니오 | 벡터 리터럴, 거리-점수 변환 |
| `tests/test_graph.py` | 아니오 | 라우팅 규칙, 그래프 전체 흐름, 프롬프트 격리, 근거 없을 때 LLM 미호출 |
| `tests/test_experiment.py` | 아니오 | 채점 로직, 결과 기록, 자격증명 미포함 |
| `tests/test_db_connection.py` | 예 | pgvector 설치, embedding 차원, 검색 SQL 실행 |
