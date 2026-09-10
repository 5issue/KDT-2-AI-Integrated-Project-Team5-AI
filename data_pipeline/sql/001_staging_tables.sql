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
    image_url      TEXT,
    PRIMARY KEY (source_type, source_id)
);

-- 조리 단계. recipe.description 에 순서를 몰아넣지 않고 단계로 분리합니다.
-- instruction 과 image_url 이 둘 다 비면 recipe_step 의 CHECK 에 걸리므로 적재 전에 거릅니다.
CREATE UNLOGGED TABLE IF NOT EXISTS staging_recipe_step (
    source_type TEXT    NOT NULL,
    source_id   TEXT    NOT NULL,
    step_no     INTEGER NOT NULL,
    instruction TEXT,
    image_url   TEXT,
    PRIMARY KEY (source_type, source_id, step_no)
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

-- 마스터 보강용. 공공 영양성분 데이터의 대표식품과 하위 분류 별칭이 들어옵니다.
CREATE UNLOGGED TABLE IF NOT EXISTS staging_ingredient_master (
    source_identity_key TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    is_raw_material     BOOLEAN NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}'
);

-- 카테고리. parent_id 는 raw 에 없으므로 경로 문자열로 받아 적재 때 해석합니다.
CREATE UNLOGGED TABLE IF NOT EXISTS staging_category (
    path          TEXT PRIMARY KEY,   -- 루트부터 자기까지. 부모를 찾는 키가 됩니다
    parent_path   TEXT NOT NULL DEFAULT '',
    category_type TEXT NOT NULL,
    name          TEXT NOT NULL,
    depth         INTEGER NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_product (
    source_type       TEXT NOT NULL,
    source_product_id TEXT NOT NULL,
    name              TEXT NOT NULL,
    price             NUMERIC(12, 2) NOT NULL,
    product_type      TEXT NOT NULL,
    category_path     TEXT,
    storage_type      TEXT,
    origin_country    TEXT,
    weight_g          NUMERIC(10, 2),
    unit_count        INTEGER,
    sku               TEXT,
    stock_quantity    INTEGER,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source_type, source_product_id)
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_product_ingredient (
    source_type       TEXT NOT NULL,
    source_product_id TEXT NOT NULL,
    normalized_name   TEXT NOT NULL,
    ingredient_id     BIGINT,             -- 상품명으로 유추한 경우 여기에 직접 담깁니다
    role              TEXT NOT NULL DEFAULT 'PRIMARY',
    quantity_g        NUMERIC(10, 2),
    ratio             NUMERIC(8, 5),
    PRIMARY KEY (source_type, source_product_id, normalized_name)
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_embedding (
    target_table TEXT NOT NULL,
    target_key   TEXT NOT NULL,
    embedding    VECTOR(1536) NOT NULL,
    PRIMARY KEY (target_table, target_key)
);

-- staging 스키마가 바뀌면 CREATE IF NOT EXISTS 로는 컬럼이 추가되지 않습니다.
-- 이미 만들어진 테이블을 따라잡기 위한 보정입니다. staging 은 언제든 다시 채울 수 있으므로
-- 컬럼을 더하는 것만으로 충분합니다.
ALTER TABLE staging_product_ingredient ADD COLUMN IF NOT EXISTS ingredient_id BIGINT;
ALTER TABLE staging_recipe ADD COLUMN IF NOT EXISTS image_url TEXT;
