-- staging 테이블. 2단계 추출 결과와 3단계 매칭 결과가 여기로 COPY 됩니다.
-- UNLOGGED: WAL 을 남기지 않아 적재가 빠릅니다. 언제든 다시 채울 수 있으므로 문제되지 않습니다.

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
    PRIMARY KEY (source_type, source_id)
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_recipe_ingredient (
    source_type     TEXT    NOT NULL,
    source_id       TEXT    NOT NULL,
    line_no         INTEGER NOT NULL,
    raw_text        TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    normalized_name TEXT    NOT NULL,
    quantity        NUMERIC(10, 3),
    unit            TEXT,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    purpose         TEXT,
    PRIMARY KEY (source_type, source_id, line_no)
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_storage_guideline (
    source_item_id       TEXT NOT NULL,
    source_food_name     TEXT NOT NULL,
    source_food_subtitle TEXT,
    normalized_name      TEXT NOT NULL,
    source_slot          TEXT NOT NULL,
    storage_location     TEXT NOT NULL,
    storage_context      TEXT NOT NULL,
    duration_min         NUMERIC(10, 2),
    duration_max         NUMERIC(10, 2),
    duration_unit        TEXT,
    duration_text        TEXT NOT NULL,
    storage_tips         TEXT,
    PRIMARY KEY (source_item_id, source_slot)
);

-- 3단계 결과. 정확 일치와 LLM 매칭이 method 로 구분됩니다.
CREATE UNLOGGED TABLE IF NOT EXISTS staging_ingredient_match (
    normalized_name TEXT   PRIMARY KEY,
    ingredient_id   BIGINT NOT NULL,
    matched_name    TEXT   NOT NULL,
    method          TEXT   NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_embedding (
    target_table TEXT NOT NULL,
    target_key   TEXT NOT NULL,
    embedding    VECTOR(1536) NOT NULL,
    PRIMARY KEY (target_table, target_key)
);
