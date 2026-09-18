"""3단계 산출물을 Neon 에 벌크 적재합니다.

입력은 파이프라인이 남긴 중간 산출물 두 가지뿐입니다.

- `artifacts/records/<dataset>.jsonl` : 2단계 추출 결과
- `artifacts/ingredient_matches.json` : 3단계 매칭 결과

## 세 조각

    models   STAGING_* 컬럼 순서 + StagingRows / LoadReport
    staging  산출물 -> 행 튜플. DB 안 봄
    loader   COPY + INSERT. 여기만 DB

`load_connection_scope` 와 `run_sql_file` 은 다른 적재 모듈(`catalog`,
`recipe_catalog`, `ingredient_master`)도 씁니다. 이 패키지의 공용 진입점입니다.

한 파일 489줄이던 것을 나눈 것입니다. 기존 import 경로를 그대로 쓸 수 있게 여기서
다시 내보냅니다.
"""

from data_pipeline.load.bulk_insert.loader import (
    REQUIRED_INDEXES,
    SQL_STEPS,
    assert_prerequisites,
    copy_staging,
    load_connection_scope,
    run_load,
    run_sql_file,
)
from data_pipeline.load.bulk_insert.models import (
    STAGING_MATCH_COLUMNS,
    STAGING_RECIPE_COLUMNS,
    STAGING_RECIPE_INGREDIENT_COLUMNS,
    STAGING_RECIPE_STEP_COLUMNS,
    STAGING_STORAGE_COLUMNS,
    LoadReport,
    StagingRows,
)
from data_pipeline.load.bulk_insert.staging import (
    build_staging_rows,
    collect_rows,
    load_match_metadata,
)

__all__ = [
    "REQUIRED_INDEXES",
    "SQL_STEPS",
    "STAGING_MATCH_COLUMNS",
    "STAGING_RECIPE_COLUMNS",
    "STAGING_RECIPE_INGREDIENT_COLUMNS",
    "STAGING_RECIPE_STEP_COLUMNS",
    "STAGING_STORAGE_COLUMNS",
    "LoadReport",
    "StagingRows",
    "assert_prerequisites",
    "build_staging_rows",
    "collect_rows",
    "copy_staging",
    "load_connection_scope",
    "load_match_metadata",
    "run_load",
    "run_sql_file",
]
