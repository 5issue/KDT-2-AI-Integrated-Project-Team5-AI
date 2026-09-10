# queries

추천 비즈니스 로직 SQL 카탈로그입니다. **한 파일 = 한 쿼리** 입니다.

```
queries/
├── _template/          예시 3종. 새로 시작할 때 복사해서 쓰세요.
├── <github_id>/        각자 폴더 (예: openleeworld/)
└── ...
```

작업 시작할 때 본인 github id 로 폴더를 만들고, `recsys_sql/.env` 의 `QUERY_OWNER` 에 같은 값을
넣으면 기본 대상이 본인 폴더로 잡힙니다. 폴더가 갈려 있어 3명이 같은 파일을 건드릴 일이 없습니다.

## 파일 규칙

파일 맨 위 주석에 메타데이터를 적습니다. 파일명(`.sql` 제외)과 `name` 이 같아야 합니다.

```sql
-- name: fridge_recipe_match
-- owner: openLeeWorld
-- description: 냉장고 재료로 만들 수 있는 레시피를 커버리지 순으로 추천
-- params: user_id:int, min_coverage:float, max_results:int

SELECT ...
```

`params` 에 쓸 수 있는 타입: `int`, `float`, `str`, `bool`, `date`, `list[int]`, `list[str]`

## 카탈로그가 자동으로 막는 것

`uv run pytest recsys_sql/tests` 를 돌리면 폴더 안 모든 `.sql` 에 대해 아래를 검사합니다.
DB 연결 없이도 도는 검사라 PR 올리기 전에 바로 확인할 수 있습니다.

- 헤더에 `name` / `owner` / `description` 이 있는가
- 파일명과 `name` 이 같은가, 카탈로그 전체에서 이름이 유일한가
- `params` 선언과 본문의 `:파라미터` 가 정확히 일치하는가 (오타로 빠뜨린 바인딩을 잡습니다)
- `INSERT`/`UPDATE`/`DELETE`/`DROP` 등 쓰기 구문이 섞이지 않았는가 (카탈로그는 읽기 전용)

값은 항상 바인딩(`:name`)으로 넘어갑니다. 문자열 포매팅으로 값을 끼워 넣지 마세요.

## DB 를 붙였을 때 추가로 도는 검사

`.env` 에 `DATABASE_URL` 이 있으면 `@pytest.mark.db` 테스트가 켜집니다.

- 쿼리가 실제로 실행되는가 (문법/컬럼명 오류를 잡습니다)
- `EXPLAIN` 에 `FORBID_SEQ_SCAN_ON` 테이블의 Seq Scan 이 없는가
- `QUERY_TIMEOUT_SECONDS` 안에 끝나는가

테스트는 트랜잭션 안에서 돌고 끝나면 롤백하므로 DB 에 흔적이 남지 않습니다.
그래도 각자 Neon 브랜치를 쓰세요. main 브랜치에 직접 붙지 않습니다.
