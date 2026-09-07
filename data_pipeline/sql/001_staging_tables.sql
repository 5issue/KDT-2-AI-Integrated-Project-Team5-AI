-- staging 테이블 생성.
-- 목적: Batch API 파싱 결과를 asyncpg COPY 로 한 번에 밀어넣은 뒤,
--       집합 연산(INSERT ... SELECT)으로 본 테이블에 반영하기 위한 착륙장입니다.
-- UNLOGGED: WAL 을 남기지 않아 적재가 빠릅니다. 크래시 시 내용이 날아가지만
--           staging 은 언제든 다시 채울 수 있으므로 문제되지 않습니다.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE UNLOGGED TABLE IF NOT EXISTS staging_recipe (
    source_type    TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    name           TEXT NOT NULL,
    description    TEXT,
    cuisine_type   TEXT,
    difficulty     TEXT,
    prep_time_min  INTEGER,
    cook_time_min  INTEGER,
    servings       NUMERIC(5, 2),
    cooking_method TEXT,
    nutrition      JSONB  NOT NULL DEFAULT '{}'::jsonb,
    tags           TEXT[] NOT NULL DEFAULT '{}',
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_type, source_id)
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_recipe_ingredient (
    source_id       TEXT    NOT NULL,
    line_no         INTEGER NOT NULL,
    raw_text        TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    normalized_name TEXT    NOT NULL,
    quantity        NUMERIC(10, 3),
    unit            TEXT,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    role            TEXT    NOT NULL DEFAULT 'PRIMARY',
    purpose         TEXT,
    PRIMARY KEY (source_id, line_no)
);

-- 파싱된 재료명 -> 기존 ingredient 마스터의 ingredient_id 매핑.
-- ingredient 는 K-FIND 코드 체계로 큐레이션된 마스터라 파이프라인이 새 행을 만들지 않습니다.
-- 여기서 매칭만 하고, 못 찾은 것은 staging_unmatched_ingredient 로 보냅니다.
CREATE UNLOGGED TABLE IF NOT EXISTS staging_ingredient_match (
    normalized_name TEXT   PRIMARY KEY,
    ingredient_id   BIGINT NOT NULL,
    matched_name    TEXT   NOT NULL,
    match_type      TEXT   NOT NULL
);

-- 마스터에 없어서 사람이 검토해야 하는 재료.
CREATE UNLOGGED TABLE IF NOT EXISTS staging_unmatched_ingredient (
    normalized_name  TEXT PRIMARY KEY,
    sample_raw_text  TEXT NOT NULL,
    sample_name      TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL,
    recipe_count     INTEGER NOT NULL
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_embedding (
    target_table TEXT NOT NULL,
    target_key   TEXT NOT NULL,
    embedding    VECTOR(1536) NOT NULL,
    PRIMARY KEY (target_table, target_key)
);
