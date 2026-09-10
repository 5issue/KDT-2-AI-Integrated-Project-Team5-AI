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

INSERT INTO ingredient (
    ingredient_id, name, normalized_name, is_raw_material, aliases, source_identity_key
)
SELECT COALESCE(
           (SELECT MAX(ingredient_id) FROM ingredient),
           0
       ) + ROW_NUMBER() OVER (ORDER BY sim.source_identity_key),
       LEFT(sim.name, 255),
       LEFT(sim.normalized_name, 255),
       sim.is_raw_material,
       sim.aliases,
       sim.source_identity_key
FROM staging_ingredient_master sim
WHERE NOT EXISTS (
    SELECT 1 FROM ingredient i WHERE i.source_identity_key = sim.source_identity_key
);

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
