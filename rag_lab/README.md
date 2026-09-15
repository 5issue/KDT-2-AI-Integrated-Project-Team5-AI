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

|             | 지금                                  | 필요한 것                                |
| ----------- | ------------------------------------- | ---------------------------------------- |
| 진입점      | `experiments/<owner>/` 스크립트 + CLI | 서빙이 부를 수 있는 함수 하나            |
| 의존성 주입 | 실험이 직접 조립                      | 서빙이 자기 풀·클라이언트를 넘길 수 있게 |
| DB 연결     | `rag_lab/.env`                        | 호출자가 넘긴 것을 쓰도록                |
| 검색 SQL    | `retrieval.py` 안                     | 그대로 두어도 되고, 카탈로그로 빼도 됨   |

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

## 공급자 갈아끼우기

모델 비교가 목적인 폴더라 공급자도 `.env` 한 줄로 바뀌어야 합니다.
`OPENAI_*` 대신 `LLM_*` 을 씁니다.

```bash
LLM_PROVIDER=openai            # openai | openrouter | anthropic | custom
LLM_API_KEY=
LLM_MODEL=gpt-4.1-mini
LLM_EMBEDDING_MODEL=text-embedding-3-small
```

OpenAI, OpenRouter, Together, Groq, vLLM 은 전부 OpenAI 호환 API 라 `base_url` 만
다릅니다. 그래서 공급자를 `(base_url, 헤더, 임베딩 지원 여부)` 로만 적어 두면
클라이언트 하나로 전부 붙습니다. 목록은 `src/rag_lab/providers.py` 에 있고,
OpenAI 호환이면 한 줄이면 추가됩니다.

**임베딩은 채팅과 다른 곳에서 받을 수 있습니다.** OpenRouter 와 Anthropic 은 임베딩
엔드포인트가 없어서, 채팅만 거기서 받고 임베딩은 따로 보내는 조합이 흔합니다.

```bash
LLM_PROVIDER=openrouter
LLM_MODEL=anthropic/claude-sonnet-4      # OpenRouter 는 접두사가 필요합니다
LLM_EMBEDDING_PROVIDER=openai            # 임베딩만 다른 곳으로
LLM_EMBEDDING_API_KEY=                   # 필수입니다. 아래 참고
```

**공급자나 주소를 따로 지정하면 키도 따로 받습니다.** 채팅 키를 물려주면 그 키가 다른
회사 엔드포인트로 그대로 나갑니다. 한 번 나간 키는 회수할 수 없어서, 편의보다 이쪽을
먼저 뒀습니다. 아무것도 지정하지 않았을 때만 채팅 설정을 물려받습니다(같은 서버니까요).

**`BASE_URL` 은 공인 호스트면 https 만 받습니다.** 그 주소로 API 키와 사용자 질문이 함께
나가는데, 평문이면 경로 중간에서 그대로 읽힙니다. 평문 http 는 루프백·사설망·단일
이름(도커/쿠버네티스 서비스명)에서만 허용합니다. 개발기와 컨테이너 사이는 그대로 됩니다.

self-hosted(TEI 로 `BAAI/bge-m3` 등)는 `custom` 입니다.

```bash
LLM_EMBEDDING_PROVIDER=custom
LLM_EMBEDDING_BASE_URL=http://localhost:8080/v1
LLM_EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024                       # 아래 주의 참고
```

조립 시점에 막는 것 두 가지입니다. 실험을 한참 돌린 뒤 404 를 보면 늦습니다.

- 임베딩을 줄 수 없는 공급자로 임베딩 클라이언트를 만들면 바로 실패
- `custom` 인데 `BASE_URL` 이 비어 있으면 바로 실패 (조용히 OpenAI 로 가지 않습니다)

**`EMBEDDING_DIM` 을 바꾸려면 DB 도 함께 바꿔야 합니다.** `recipe`/`product`/`ingredient`
의 `embedding` 이 `VECTOR(1536)` 입니다. `BAAI/bge-m3` 는 1024 라 컬럼 마이그레이션이
필요하고, 기존 임베딩도 전부 다시 만들어야 합니다. 클라이언트가 응답 차원을 확인해서
설정과 다르면 그 자리에서 실패시킵니다.

실험 결과에는 공급자도 함께 기록됩니다. 같은 모델명이라도 어디를 거쳤는지에 따라
결과가 달라져서, 그 값이 없으면 지난 실험과 비교할 수 없습니다.

예전 이름(`OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`, `OPENAI_EMBEDDING_MODEL`)도 계속
읽습니다. 로컬 `.env` 가 조용히 깨지지 않게 두는 것이고, 새로 쓰는 값은 `LLM_*` 입니다.

`data_pipeline` 은 OpenAI Batch API 에 묶여 있어 이 추상화를 쓰지 않습니다.
배치 제출·폴링·수거가 OpenAI 고유 엔드포인트(`/v1/batches`)라 공급자를 바꾸려면
파이프라인 자체를 다시 써야 합니다.

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

| 값                | 의미                                              |
| ----------------- | ------------------------------------------------- |
| `TOP_K`           | 벡터 검색으로 가져올 후보 수                      |
| `SCORE_THRESHOLD` | 이 점수 아래는 근거로 쓰지 않음 (코사인 기준 0~1) |
| `DISTANCE_METRIC` | `cosine` / `l2` / `inner_product`                 |

거리는 지표마다 범위가 달라서 `retrieval.distance_to_score` 가 0~1 에 가깝게 맞춰 줍니다.
cosine 은 `1 - 거리`, l2 는 `1 / (1 + 거리)`, inner_product 는 부호만 뒤집습니다.
지표를 바꾸면 `SCORE_THRESHOLD` 의 의미도 달라지니 같이 조정하세요.

## 테스트

```bash
uv run pytest rag_lab/tests -q
```

| 파일                          | DB/API 필요 | 하는 일                                                               |
| ----------------------------- | ----------- | --------------------------------------------------------------------- |
| `tests/test_retrieval.py`     | 아니오      | 벡터 리터럴, 거리-점수 변환                                           |
| `tests/test_graph.py`         | 아니오      | 라우팅 규칙, 그래프 전체 흐름, 프롬프트 격리, 근거 없을 때 LLM 미호출 |
| `tests/test_experiment.py`    | 아니오      | 채점 로직, 결과 기록, 자격증명 미포함                                 |
| `tests/test_db_connection.py` | 예          | pgvector 설치, embedding 차원, 검색 SQL 실행                          |

## TODO 리스트?

langfuse(시간없으면 langsmith로 빠르게 대체, monitoring폴더를 파고 거기서 유연하게 교체하게끔 구성하면 좋겠음) 기반 monitoring, token usage 추적 등
ragas 기반 골든 데이터셋으로 평가

rag 실험 환경 고도화 방안 적용 -> 실험 후 최종 적용 기준 마련
