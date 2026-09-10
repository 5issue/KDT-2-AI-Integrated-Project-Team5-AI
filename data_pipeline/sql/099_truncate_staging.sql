-- 적재가 끝난 뒤 staging 을 비웁니다. 본 테이블은 건드리지 않습니다.
TRUNCATE staging_recipe,
         staging_recipe_step,
         staging_recipe_ingredient,
         staging_storage_guideline,
         staging_ingredient_match,
         staging_ingredient_master,
         staging_embedding;
