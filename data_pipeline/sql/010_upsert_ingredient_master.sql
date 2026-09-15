-- staging_ingredient_master -> ingredient 보강.
--
-- 이 파일은 파이프라인에서 **마스터에 쓰는 유일한 경로**입니다. 나머지 적재 SQL 은
-- 여전히 매칭만 하고 마스터에 행을 만들지 않습니다.
--
-- 기존 736행은 원재료성식품(K-FIND:...)이고, 가공식품은 K-FIND-P:... 로 들어옵니다.
-- 대표식품코드가 원재료와 10건 겹쳐서 접두사로 갈라 둡니다.
--
-- 이름은 덮어쓰지 않습니다. 팀이 손으로 다듬은 표기를 공공데이터 표기로 되돌리면
-- 곤란하기 때문입니다. 새로 만드는 행에만 쓰고, 기존 행은 aliases 만 합칩니다.

-- ingredient_id 는 시퀀스에 맡깁니다. 예전에 MAX+ROW_NUMBER 로 직접 지정했더니
-- 시퀀스가 736 에 멈춘 채 행이 1018 까지 늘어, 다음 삽입이 기존 id 와 충돌할 뻔했습니다.
-- (storage_guideline 은 시퀀스가 없어 그쪽만 MAX+ROW_NUMBER 를 씁니다.)
INSERT INTO ingredient (
    name, normalized_name, is_raw_material, aliases, is_pantry, source_identity_key
)
SELECT LEFT(sim.name, 255),
       LEFT(sim.normalized_name, 255),
       sim.is_raw_material,
       sim.aliases,
       sim.is_pantry,
       sim.source_identity_key
FROM staging_ingredient_master sim
WHERE NOT EXISTS (
    SELECT 1 FROM ingredient i WHERE i.source_identity_key = sim.source_identity_key
);

-- 재료를 카테고리에 붙입니다. 006 이 만든 category 의 metadata.path 로 되짚습니다.
-- 식품대분류(K-FIND 코드)가 그대로 분류가 되므로 따로 판단할 것이 없습니다.
UPDATE ingredient i
SET ingredient_category_id = cat.category_id
FROM staging_ingredient_master sim
JOIN category cat ON cat.metadata ->> 'path' = sim.category_path
                 AND cat.category_type = 'INGREDIENT'
WHERE i.source_identity_key = sim.source_identity_key
  AND sim.category_path IS NOT NULL
  AND i.ingredient_category_id IS DISTINCT FROM cat.category_id;

-- 상비재료 표시는 큐레이션 목록이 정본이라 기존 행에도 반영합니다.
UPDATE ingredient i
SET is_pantry = sim.is_pantry
FROM staging_ingredient_master sim
WHERE i.source_identity_key = sim.source_identity_key
  AND i.is_pantry IS DISTINCT FROM sim.is_pantry;

-- 기존 행에는 별칭만 더합니다. 중복은 제거하고, 이미 있는 별칭은 그대로 둡니다.
UPDATE ingredient i
SET aliases = merged.aliases
FROM (
    SELECT i2.ingredient_id,
           ARRAY(
               SELECT DISTINCT a
               FROM UNNEST(i2.aliases || sim.aliases) AS a
               WHERE BTRIM(a) <> ''
               ORDER BY a
           ) AS aliases
    FROM ingredient i2
    JOIN staging_ingredient_master sim
      ON sim.source_identity_key = i2.source_identity_key
) AS merged
WHERE i.ingredient_id = merged.ingredient_id
  AND i.aliases IS DISTINCT FROM merged.aliases;

-- 과거 실행이 id 를 직접 지정해 시퀀스가 뒤처져 있으면 여기서 맞춥니다.
-- 이 줄이 없으면 다음에 시퀀스로 넣는 행이 기존 id 와 충돌합니다.
SELECT setval(
    pg_get_serial_sequence('ingredient', 'ingredient_id'),
    GREATEST((SELECT COALESCE(MAX(ingredient_id), 1) FROM ingredient), 1)
);
