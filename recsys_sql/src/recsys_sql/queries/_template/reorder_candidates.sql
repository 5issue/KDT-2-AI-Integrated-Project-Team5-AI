-- name: reorder_candidates
-- owner: _template
-- description: 마지막 구매 후 일정 기간이 지난 단골 상품을 재구매 후보로 추천
-- params: user_id:int, days_since:int, max_results:int
--
-- user_product_affinity 는 배치로 갱신되는 집계 테이블입니다.
-- 지금 냉장고에 있는 상품은 후보에서 뺍니다.

SELECT p.product_id,
       p.name AS product_name,
       p.price,
       p.storage_type,
       upa.purchase_count,
       upa.last_purchased_at,
       upa.affinity_score,
       DATE_PART('day', NOW() - upa.last_purchased_at)::int AS days_elapsed
FROM user_product_affinity upa
JOIN product p ON p.product_id = upa.product_id
WHERE upa.user_id = :user_id
  AND p.is_active
  -- stock_quantity 는 NULL 허용이고, **NULL 은 품절이 아니라 "수량을 모른다" 입니다**
  -- (정규화 가이드 4.3: 품절이 명시된 경우만 0 으로 저장). 모르는 것을 품절로 치면
  -- 지금 적재분(2,553개 전부 NULL)에서는 결과가 항상 빈손이 됩니다.
  AND (p.stock_quantity IS NULL OR p.stock_quantity > 0)
  AND upa.last_purchased_at IS NOT NULL
  AND upa.last_purchased_at < NOW() - MAKE_INTERVAL(days => :days_since)
  AND NOT EXISTS (
      SELECT 1
      FROM user_fridge uf
      WHERE uf.user_id = upa.user_id
        AND uf.product_id = p.product_id
        AND (uf.expires_at IS NULL OR uf.expires_at >= NOW())
  )
ORDER BY upa.affinity_score DESC,
         upa.purchase_count DESC,
         upa.last_purchased_at ASC,
         p.product_id ASC
LIMIT :max_results;
