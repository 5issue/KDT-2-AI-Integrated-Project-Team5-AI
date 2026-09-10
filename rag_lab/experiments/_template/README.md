# _template

실험 폴더 템플릿입니다. 본인 github id 로 폴더를 만들고 이걸 복사해서 시작하세요.

```
experiments/
├── _template/
│   ├── questions.jsonl   질문 세트
│   └── results/          실행 결과 JSONL 이 쌓이는 곳
└── <github_id>/
```

## 질문 세트 형식

한 줄에 질문 하나입니다. `expected_route` 와 `expected_doc_ids` 는 선택이고,
넣으면 실험 리포트에 라우팅 정확도와 검색 적중률이 함께 찍힙니다.

```json
{"question": "김치로 뭐 해먹지", "expected_route": "recipe", "expected_doc_ids": [12, 34]}
```

## 실행

```bash
uv run rag-lab experiment --name topk5 --cases rag_lab/experiments/_template/questions.jsonl
```

`.env` 의 `TOP_K`, `SCORE_THRESHOLD`, `DISTANCE_METRIC` 을 바꿔가며 같은 질문 세트를 돌리면
결과 JSONL 첫 줄에 그때의 파라미터가 함께 남습니다. 나중에 어떤 설정의 결과였는지
헷갈리지 않게 하기 위한 것입니다. API 키나 DB URL 은 기록에 남지 않습니다.

`results/` 는 각자 것만 쌓이므로 세 명이 같은 파일을 건드릴 일이 없습니다.
