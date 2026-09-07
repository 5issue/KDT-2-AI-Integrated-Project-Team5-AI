-- 파싱된 재료명을 기존 ingredient 마스터에 매칭합니다. 새 재료를 만들지 않습니다.
--
-- ingredient 는 K-FIND 코드 체계(source_identity_key = 'K-FIND:01:01018:국수:MIDDLE:소면')로
-- 큐레이션된 마스터 테이블입니다. 레시피 파싱 결과로 여기에 행을 자동 추가하면 키 체계가
-- 섞여 마스터가 지저분해지므로, 매칭만 하고 못 찾은 것은 사람이 검토하도록 모아 둡니다.
--
-- normalized_name 은 유니크가 아닙니다(736행 중 18건 중복: 생강 3건 등).
-- 그래서 결정적인 우선순위로 하나를 고릅니다.
--   1) normalized_name 정확히 일치 > name 일치 > aliases 포함
--   2) 상위 항목 우선 (parent_ingredient_id IS NULL)
--   3) 그래도 같으면 낮은 ingredient_id

TRUNCATE staging_ingredient_match, staging_unmatched_ingredient;

WITH wanted AS (
    SELECT DISTINCT normalized_name
    FROM staging_recipe_ingredient
    WHERE BTRIM(normalized_name) <> ''
),
candidate AS (
    SELECT w.normalized_name,
           i.ingredient_id,
           i.name AS matched_name,
           CASE
               WHEN LOWER(BTRIM(i.normalized_name)) = w.normalized_name THEN 'normalized_name'
               WHEN LOWER(BTRIM(i.name)) = w.normalized_name           THEN 'name'
               ELSE 'alias'
           END AS match_type
    FROM wanted w
    JOIN ingredient i
      ON LOWER(BTRIM(i.normalized_name)) = w.normalized_name
      OR LOWER(BTRIM(i.name)) = w.normalized_name
      OR EXISTS (
          SELECT 1 FROM UNNEST(i.aliases) AS alias
          WHERE LOWER(BTRIM(alias)) = w.normalized_name
      )
)
INSERT INTO staging_ingredient_match (normalized_name, ingredient_id, matched_name, match_type)
SELECT DISTINCT ON (c.normalized_name)
       c.normalized_name, c.ingredient_id, c.matched_name, c.match_type
FROM candidate c
JOIN ingredient i ON i.ingredient_id = c.ingredient_id
ORDER BY c.normalized_name,
         CASE c.match_type WHEN 'normalized_name' THEN 0 WHEN 'name' THEN 1 ELSE 2 END,
         (i.parent_ingredient_id IS NULL) DESC,
         c.ingredient_id ASC;

INSERT INTO staging_unmatched_ingredient (
    normalized_name, sample_raw_text, sample_name, occurrence_count, recipe_count
)
SELECT sri.normalized_name,
       MIN(sri.raw_text),
       MIN(sri.name),
       COUNT(*)::int,
       COUNT(DISTINCT sri.source_id)::int
FROM staging_recipe_ingredient sri
LEFT JOIN staging_ingredient_match m ON m.normalized_name = sri.normalized_name
WHERE m.normalized_name IS NULL
  AND BTRIM(sri.normalized_name) <> ''
GROUP BY sri.normalized_name;
