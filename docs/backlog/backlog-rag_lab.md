# rag_lab 남은 일

담당: `rag_lab` 담당자 + openLeeWorld(추천 문구) · 기준일 2026-09-16

# 팀원 권고 사항? (보류 — 아래 조사 결과 참고)

> **이 지시는 2026-09-16 조사로 보류됐습니다.** 아래 "조사 결과" 가 현재 결론입니다.
> 원문은 무엇을 요청받았는지 남기려고 그대로 둡니다.

rag_lab에 langfuse cloud관련 세팅하여 연결 & 모니터링하기
langfuse 버전은 (TODO: 조사중)로 하고, 해당 공식문서 파이썬 SDK를 기반으로 오류없게 구축한다. 또한 ragas 버전 (TODO: 조사중)을 기반으로 llm-as-a-judge based on golden dataset, ragas 라이브러리의 표준 평가 지표를 따라 rag 평가도 병행하므로 세팅한다. -> 팀원에게 전달

## 조사 결과 (2026-09-16, openLeeWorld)

`ragas==0.3.9` / `langfuse>=4.0.0` 을 실제로 재 봤습니다. **결론: 지금은 둘 다 넣지 않고
끼울 자리만 만들어 뒀습니다.** 근거 전문은
`ai_context/aI가 쓴 문서/rag_lab 실험 환경 세팅과 구현 계획.md` **8절**.

요약 넷입니다.

| | |
| --- | --- |
| **무게** | `uv pip compile` 결과 의존성 106개, 그중 **57개가 새로 들어옴** (`datasets`, `pandas`, `scipy`, `scikit-network`, `pillow`, langchain 5종, OpenTelemetry 6종) |
| **쓸 수 있는 지표** | **`AspectCritic` 하나.** 나머지는 `reference`(정답 문구) 또는 `retrieved_contexts` 를 요구하는데 우리는 **둘 다 없습니다** |
| **실측** | 판정이 틀리는 원인이 **모호한 루브릭 + 자기 모델 자기 채점**이었습니다. ragas 를 깔아도 그 둘은 그대로입니다 |
| **langfuse** | 25건 배치 실험에는 값이 적습니다. **`serving` 이 실트래픽을 받을 때** 제값을 합니다 |

`AspectCritic` 은 *"이 응답이 <정의>를 만족하는가"* 를 LLM 에 한 번 묻는 것이고,
`rag_lab.recommendation.score_reason()` 이 같은 일을 8항목으로 합니다. 그 함수의
입력(`RecommendationCase` + 문구)과 출력(`RubricScore`)을 고정해 뒀으니
**안쪽만 ragas 의 `RubricsScore`(우리 `RubricScore` 와 다른 클래스입니다) 나
`AspectCritic` 여러 개로 교체해도 실험 코드는 그대로입니다.**

### 도입 조건 (이때는 넣으세요)

| 조건 | 현재 |
| --- | --- |
| 골든 데이터셋(정답 문구)을 만들기로 결정 | 없음 |
| 추천 문구에 검색을 붙이기로 결정 | 미사용 |
| 평가 케이스 100건 초과 | 25건 |
| `serving` 이 실사용 트래픽을 받음 | 미배포 |

넷 다 아닙니다. 넣기로 한다면 순서는 계획 8-8 참고 -
**선택적 의존성(`[eval]`) + `AspectCritic` 만 + 판정 모델 분리 + langfuse 는 serving 부터.**

### 버전 주의

팀원 메모대로 **self-hosted runner 는 langfuse v3 이하**입니다. v4 SDK 로 짜 놓고
v3 서버에 붙이면 `start_as_current_observation` 계열 API 가 안 맞습니다.
**계측 코드를 쓰기 전에 호스팅 방식을 먼저 정하세요.** Cloud 면 v4 그대로 됩니다.

---

## 지금까지 된 것

| | 상태 |
| --- | --- |
| 임베딩 (recipe / product / ingredient) | 4,667행 100%. `data-pipeline embed` |
| 라우팅 + 검색 그래프 (`ask`) | 작동. 규칙 기반 라우터 |
| LLM 공급자 추상화 | `.env` 의 `LLM_*` 로 교체. OpenRouter 검증됨 |
| **추천 문구 경로 (`recommend`)** | **작동.** 상황 25건 + 자동 검사 6종 + 루브릭 8항목 채점 |
| **평가 루브릭** | `rag 평가 지표 종류.md` 7절 그대로. 0~10점 x 8항목, 평균 7.5 통과 |
| **판정 모델 분리** | `JUDGE_*` 4종. 자가 평가를 조립 시점에 거부 |
| 서빙 진입점 | `from rag_lab import recommendation_reason` (실제 위치는 `recommendation/core.py`) |

```bash
uv run rag-lab recommend --name baseline --cases rag_lab/experiments/<id>/cases.jsonl
JUDGE_MODEL=openai/gpt-4.1 uv run rag-lab recommend --name v5 --judge --cases ...
```

현재 `openai/gpt-4.1-mini` 생성 / `openai/gpt-4.1` 판정으로 **루브릭 평균 8.24,
통과율 84%** 입니다(통과선 7.5). 항목별 평균과 판정 모델이 결과 JSONL 첫 줄에 남습니다.

실험 기록은 `rag_lab/experiments/openLeeWorld/README.md`.

## 남은 일

| | 할 일 | 왜 |
| --- | --- | --- |
| 1 | 판정자 교차 검증(다른 계열 모델로 한 번) | 점수가 판정자에 얼마나 좌우되는지 모릅니다. **그 전까지 8.24 를 절대 점수로 읽지 마세요** |
| 2 | `differentiation` 7.0 올리기 | 8항목 중 가장 낮습니다. 부족 재료가 많은 상황에서 문장이 상투적으로 돌아갑니다 |
| 3 | 검색 사용/미사용 비교 | 계획 4-4 의 진짜 질문. 지금 추천 문구는 검색을 안 씁니다 |
| 4 | `serving` 에 `recommendation_reason` 연결 | api_spec 의 `recommendation_reason` 필드 추가가 선행 |
| 5 | `rag_lab/Dockerfile` (0바이트) | `서빙 패키징과 ECR 배포 계획.md` |

**채점기를 못 믿으면 프롬프트를 고칠 수 없습니다.** 이번에 채점기 버그 네 개를 고치는
동안 자동 검사 수치가 88% -> 100% -> 80% -> 100% 로 흔들렸고, 그 구간의 비교는 전부
무의미했습니다. 수치가 움직이면 **문구가 바뀐 것인지 채점기가 바뀐 것인지부터** 가르세요.

## 알아 둘 것

- **추천 문구는 DB 를 열지 않습니다.** 입력이 `my_recipe_candidates` 가 준 구조화된
  값이라 벡터 검색을 한 번 더 돌 이유가 없습니다. `ask` 경로와 갈립니다.
- **실험 파일과 결과는 사람별로 갈라 둡니다.** `experiments/<github_id>/cases.jsonl` 과
  `experiments/<github_id>/results/`. `.env` 의 `EXPERIMENT_OWNER` 가 기본 경로를 정합니다.
- **`recommendation/` 은 네 모듈로 나뉘어 있고, 서빙이 쓰는 것은 `core` 하나뿐입니다.**

  | 모듈 | 역할 | 서빙 |
  | --- | --- | --- |
  | `core` | 상황 -> 프롬프트 -> LLM 호출 | **씀** |
  | `checks` | 재료 환각·보유 뒤집힘·시간 조작 (LLM 무관) | 안 씀 |
  | `judge` | 루브릭 8항목 채점 | 안 씀 |
  | `experiment` | 상황 세트 실행·JSONL·재채점 | 안 씀 |

  한 파일에 있던 765줄을 나눈 것입니다. 생성 로직 자체는 짧은데 실험용 검증·리포팅이
  함께 있어 길어졌습니다. `from rag_lab.recommendation import ...` 는 그대로 동작하지만,
  새 코드는 어느 층을 쓰는지 드러나게 **모듈을 직접 지목**하세요.
- **`rag_lab/experiment.py`(질문 세트) 와 `recommendation/experiment.py`(추천 문구) 는
  다른 사람이 봅니다.** 결과 JSONL 형식과 `snapshot_params` 만 공유합니다.
- **한국어 동음이의어는 문자열 검사로 못 가립니다.** `가지`(채소/수량단위/동사),
  `있으면`(가정) 같은 것들입니다. `_AMBIGUOUS_WORDS` 에는 **실제로 부딪힌 것만**
  넣으세요. 미리 채우면 진짜 환각을 놓칩니다.
- **프롬프트 인젝션은 막혀 있습니다.** 상황을 `<situation>` 으로 감싸고 시스템
  프롬프트에 못박았습니다. `inject_00_in_note` 케이스가 이걸 고정합니다(OWASP LLM01).
- **판정 모델은 생성 모델과 달라야 합니다.** `JUDGE_MODEL` 이 비었거나 생성 모델과
  같으면 `--judge` 가 실행되지 않습니다. 기본값을 두지 않은 이유는, 기본값이 있으면
  아무도 모르는 채 자가 평가가 되기 때문입니다.
- **문구 다양성 100% 를 믿지 마세요.** 문자열 비교라 어미만 달라도 100% 입니다.
  실제로 25건 중 21건이 같은 틀("신선한 A와 B로 건강한 C를...")이었는데 100% 로
  잡혔습니다. 틀의 반복은 루브릭 `differentiation` 이 잡습니다.
- 실험 한 번(생성 25 + 채점 25)에 **$0.03 안팎**입니다. 채점 쪽이 대부분이라,
  프롬프트를 손보는 동안에는 `--judge` 없이 돌리세요. 자동 검사 6종은 LLM 없이 돕니다.
