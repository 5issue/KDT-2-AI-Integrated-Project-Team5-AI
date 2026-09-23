-- 2026-09-18 Neon production 스키마 (backups/neon-pgdump-20260918T003803Z/default.full.sql 에서 스키마만 추출).
-- 0001_baseline 이 빈 DB 에서만 실행한다. 이미 테이블이 있는 DB 는 건드리지 않는다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE public.app_user (
    user_id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.category (
    category_id bigint NOT NULL,
    category_type character varying(30) NOT NULL,
    parent_id bigint,
    name character varying(100) NOT NULL,
    depth integer DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.category_category_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.category_category_id_seq OWNED BY public.category.category_id;
CREATE TABLE public.ingredient (
    ingredient_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    normalized_name character varying(255) NOT NULL,
    ingredient_category_id bigint,
    is_raw_material boolean DEFAULT true NOT NULL,
    aliases text[] DEFAULT '{}'::text[] NOT NULL,
    nutrition jsonb DEFAULT '{}'::jsonb NOT NULL,
    embedding public.vector(1536),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    is_pantry boolean DEFAULT false NOT NULL,
    parent_ingredient_id bigint,
    source_identity_key text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);
COMMENT ON COLUMN public.ingredient.is_pantry IS '상비재료 여부. 지정한 재료만 TRUE로 설정한다.';
CREATE SEQUENCE public.ingredient_ingredient_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.ingredient_ingredient_id_seq OWNED BY public.ingredient.ingredient_id;
CREATE TABLE public.order_header (
    order_id bigint NOT NULL,
    user_id bigint NOT NULL,
    ordered_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.order_header_order_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.order_header_order_id_seq OWNED BY public.order_header.order_id;
CREATE TABLE public.order_item (
    order_id bigint NOT NULL,
    product_id bigint NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(12,2) NOT NULL
);
CREATE TABLE public.product (
    product_id bigint NOT NULL,
    sku character varying(100),
    name character varying(255) NOT NULL,
    brand_id bigint,
    category_id bigint,
    product_type character varying(30) NOT NULL,
    storage_type character varying(20),
    origin_country character varying(100),
    weight_g numeric(10,2),
    unit_count integer,
    price numeric(12,2) NOT NULL,
    stock_quantity integer,
    is_active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    embedding public.vector(1536),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    source_type character varying(30) NOT NULL,
    source_product_id character varying(100) NOT NULL
);
CREATE TABLE public.product_ingredient (
    product_id bigint NOT NULL,
    ingredient_id bigint NOT NULL,
    quantity_g numeric(10,2),
    role character varying(30) DEFAULT 'PRIMARY'::character varying NOT NULL,
    ratio numeric(8,5),
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL
);
CREATE TABLE public.product_popularity (
    period_start date NOT NULL,
    period_end date NOT NULL,
    product_id bigint NOT NULL,
    order_count integer DEFAULT 0 NOT NULL,
    quantity_sold integer DEFAULT 0 NOT NULL,
    cart_count integer DEFAULT 0 NOT NULL,
    view_count integer DEFAULT 0 NOT NULL,
    popularity_score double precision DEFAULT 0 NOT NULL
);
CREATE SEQUENCE public.product_product_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.product_product_id_seq OWNED BY public.product.product_id;
CREATE TABLE public.recipe (
    recipe_id bigint NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    category_id bigint,
    cuisine_type character varying(50),
    difficulty character varying(20),
    prep_time_min integer,
    cook_time_min integer,
    servings numeric(5,2),
    cooking_method character varying(50),
    nutrition jsonb DEFAULT '{}'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    embedding public.vector(1536),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    source_type character varying(30) NOT NULL,
    source_recipe_id character varying(100) NOT NULL
);
CREATE TABLE public.recipe_ingredient (
    recipe_id bigint NOT NULL,
    ingredient_id bigint NOT NULL,
    quantity numeric(10,3),
    unit character varying(30),
    is_required boolean DEFAULT true NOT NULL,
    purpose character varying(50),
    requirements jsonb DEFAULT '{}'::jsonb NOT NULL
);
CREATE TABLE public.recipe_product (
    recipe_id bigint NOT NULL,
    product_id bigint NOT NULL,
    recommendation_priority integer DEFAULT 0 NOT NULL,
    is_substitute boolean DEFAULT false NOT NULL,
    ingredient_id bigint NOT NULL
);
CREATE SEQUENCE public.recipe_recipe_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.recipe_recipe_id_seq OWNED BY public.recipe.recipe_id;
CREATE TABLE public.user_fridge (
    ingredient_id bigint NOT NULL,
    user_id bigint NOT NULL,
    product_id bigint NOT NULL,
    quantity numeric(10,2) NOT NULL,
    unit character varying(30) NOT NULL,
    expires_at timestamp without time zone
);
CREATE TABLE public.user_product_affinity (
    user_id bigint NOT NULL,
    product_id bigint NOT NULL,
    purchase_count integer DEFAULT 0 NOT NULL,
    last_purchased_at timestamp without time zone,
    affinity_score double precision DEFAULT 0 NOT NULL
);
ALTER TABLE ONLY public.category ALTER COLUMN category_id SET DEFAULT nextval('public.category_category_id_seq'::regclass);
ALTER TABLE ONLY public.ingredient ALTER COLUMN ingredient_id SET DEFAULT nextval('public.ingredient_ingredient_id_seq'::regclass);
ALTER TABLE ONLY public.order_header ALTER COLUMN order_id SET DEFAULT nextval('public.order_header_order_id_seq'::regclass);
ALTER TABLE ONLY public.product ALTER COLUMN product_id SET DEFAULT nextval('public.product_product_id_seq'::regclass);
ALTER TABLE ONLY public.recipe ALTER COLUMN recipe_id SET DEFAULT nextval('public.recipe_recipe_id_seq'::regclass);
ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT pk_app_user PRIMARY KEY (user_id);
ALTER TABLE ONLY public.category
    ADD CONSTRAINT pk_category PRIMARY KEY (category_id);
ALTER TABLE ONLY public.ingredient
    ADD CONSTRAINT pk_ingredient PRIMARY KEY (ingredient_id);
ALTER TABLE ONLY public.order_header
    ADD CONSTRAINT pk_order_header PRIMARY KEY (order_id);
ALTER TABLE ONLY public.order_item
    ADD CONSTRAINT pk_order_item PRIMARY KEY (order_id, product_id);
ALTER TABLE ONLY public.product
    ADD CONSTRAINT pk_product PRIMARY KEY (product_id);
ALTER TABLE ONLY public.product_ingredient
    ADD CONSTRAINT pk_product_ingredient PRIMARY KEY (product_id, ingredient_id);
ALTER TABLE ONLY public.product_popularity
    ADD CONSTRAINT pk_product_popularity PRIMARY KEY (period_start, period_end, product_id);
ALTER TABLE ONLY public.recipe
    ADD CONSTRAINT pk_recipe PRIMARY KEY (recipe_id);
ALTER TABLE ONLY public.recipe_ingredient
    ADD CONSTRAINT pk_recipe_ingredient PRIMARY KEY (recipe_id, ingredient_id);
ALTER TABLE ONLY public.recipe_product
    ADD CONSTRAINT pk_recipe_product PRIMARY KEY (recipe_id, ingredient_id, product_id);
ALTER TABLE ONLY public.user_fridge
    ADD CONSTRAINT pk_user_fridge PRIMARY KEY (ingredient_id, user_id, product_id);
ALTER TABLE ONLY public.user_product_affinity
    ADD CONSTRAINT pk_user_product_affinity PRIMARY KEY (user_id, product_id);
ALTER TABLE ONLY public.product
    ADD CONSTRAINT uq_product_sku UNIQUE (sku);
CREATE UNIQUE INDEX ingredient_source_identity_key_unique_idx ON public.ingredient USING btree (source_identity_key);
CREATE INDEX product_category_id_idx ON public.product USING btree (category_id);
CREATE INDEX product_ingredient_ingredient_id_idx ON public.product_ingredient USING btree (ingredient_id);
CREATE UNIQUE INDEX product_sku_unique_idx ON public.product USING btree (sku) WHERE (sku IS NOT NULL);
CREATE UNIQUE INDEX product_source_unique_idx ON public.product USING btree (source_type, source_product_id);
CREATE INDEX recipe_ingredient_ingredient_id_idx ON public.recipe_ingredient USING btree (ingredient_id);
CREATE UNIQUE INDEX recipe_source_unique_idx ON public.recipe USING btree (source_type, source_recipe_id);
CREATE UNIQUE INDEX uq_ingredient_source_identity_key ON public.ingredient USING btree (source_identity_key);
ALTER TABLE ONLY public.order_header
    ADD CONSTRAINT fk_app_user_to_order_header FOREIGN KEY (user_id) REFERENCES public.app_user(user_id);
ALTER TABLE ONLY public.user_fridge
    ADD CONSTRAINT fk_app_user_to_user_fridge FOREIGN KEY (user_id) REFERENCES public.app_user(user_id);
ALTER TABLE ONLY public.user_product_affinity
    ADD CONSTRAINT fk_app_user_to_user_product_affinity FOREIGN KEY (user_id) REFERENCES public.app_user(user_id);
ALTER TABLE ONLY public.product_ingredient
    ADD CONSTRAINT fk_ingredient_to_product_ingredient FOREIGN KEY (ingredient_id) REFERENCES public.ingredient(ingredient_id);
ALTER TABLE ONLY public.recipe_ingredient
    ADD CONSTRAINT fk_ingredient_to_recipe_ingredient FOREIGN KEY (ingredient_id) REFERENCES public.ingredient(ingredient_id);
ALTER TABLE ONLY public.recipe_product
    ADD CONSTRAINT fk_ingredient_to_recipe_product FOREIGN KEY (ingredient_id) REFERENCES public.ingredient(ingredient_id);
ALTER TABLE ONLY public.user_fridge
    ADD CONSTRAINT fk_ingredient_to_user_fridge FOREIGN KEY (ingredient_id) REFERENCES public.ingredient(ingredient_id);
ALTER TABLE ONLY public.order_item
    ADD CONSTRAINT fk_order_header_to_order_item FOREIGN KEY (order_id) REFERENCES public.order_header(order_id);
ALTER TABLE ONLY public.order_item
    ADD CONSTRAINT fk_product_to_order_item FOREIGN KEY (product_id) REFERENCES public.product(product_id);
ALTER TABLE ONLY public.product_ingredient
    ADD CONSTRAINT fk_product_to_product_ingredient FOREIGN KEY (product_id) REFERENCES public.product(product_id);
ALTER TABLE ONLY public.product_popularity
    ADD CONSTRAINT fk_product_to_product_popularity FOREIGN KEY (product_id) REFERENCES public.product(product_id);
ALTER TABLE ONLY public.recipe_product
    ADD CONSTRAINT fk_product_to_recipe_product FOREIGN KEY (product_id) REFERENCES public.product(product_id);
ALTER TABLE ONLY public.user_fridge
    ADD CONSTRAINT fk_product_to_user_fridge FOREIGN KEY (product_id) REFERENCES public.product(product_id);
ALTER TABLE ONLY public.user_product_affinity
    ADD CONSTRAINT fk_product_to_user_product_affinity FOREIGN KEY (product_id) REFERENCES public.product(product_id);
ALTER TABLE ONLY public.recipe_ingredient
    ADD CONSTRAINT fk_recipe_to_recipe_ingredient FOREIGN KEY (recipe_id) REFERENCES public.recipe(recipe_id);
ALTER TABLE ONLY public.recipe_product
    ADD CONSTRAINT fk_recipe_to_recipe_product FOREIGN KEY (recipe_id) REFERENCES public.recipe(recipe_id);
