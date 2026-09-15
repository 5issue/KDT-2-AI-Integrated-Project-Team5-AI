-- staging_category -> category.
--
-- raw 에는 parent_id 가 없고 경로 문자열(`수산 > 해산물/조개류`)만 있습니다.
-- 얕은 깊이부터 넣으면서 부모를 찾아 id 를 채웁니다.
--
-- category 에는 자연키 유니크 제약이 없어 ON CONFLICT 를 쓸 수 없습니다.
-- 대신 경로를 metadata.path 에 남기고 그것으로 중복을 가립니다.
-- 동시에 두 번 돌리면 중복이 생길 수 있습니다(단일 실행 전제).

DO $$
DECLARE
    lvl INTEGER;
    max_depth INTEGER;
BEGIN
    SELECT COALESCE(MAX(depth), -1) INTO max_depth FROM staging_category;
    FOR lvl IN 0..max_depth LOOP
        INSERT INTO category (category_type, parent_id, name, depth, metadata)
        SELECT sc.category_type,
               parent.category_id,
               LEFT(sc.name, 100),
               sc.depth,
               sc.metadata || jsonb_build_object('path', sc.path)
        FROM staging_category sc
        LEFT JOIN category parent
               ON parent.metadata ->> 'path' = sc.parent_path
              AND sc.parent_path <> ''
        WHERE sc.depth = lvl
          AND NOT EXISTS (
              SELECT 1 FROM category c WHERE c.metadata ->> 'path' = sc.path
          );
    END LOOP;
END $$;
