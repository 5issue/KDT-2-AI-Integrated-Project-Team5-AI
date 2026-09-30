크롤링, 실험, PoC 등 ipynb 필요시 해당 폴더에 삽입

| 노트북 | 내용 | 접속 정보 |
| --- | --- | --- |
| `ingredient_master_storage_alignment.ipynb` | 재료 마스터 정렬과 보관 가이드 보강 | 셸 환경변수 |
| `ai_safety_eval.ipynb` | 추천 이유 LLM 안전성 검증 (할루시네이션·Prompt Injection·편향, deepeval) | `notebooks/.env` (`.env.example` 참고) |

`.gitignore` 가 `*.ipynb` 를 제외하므로, 공유할 노트북은 `.gitignore` 에 예외(`!notebooks/<이름>.ipynb`)를 추가합니다.
