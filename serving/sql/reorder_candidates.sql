-- name: reorder_candidates
-- promoted-from: recsys_sql/queries/_template/reorder_candidates.sql
-- description: 마지막 구매 후 일정 기간이 지난 단골 상품을 재구매 후보로 추천
-- params: $1 = user_id (bigint), $2 = days_since (int), $3 = max_results (int)
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
WHERE upa.user_id = $1
  AND p.is_active
  -- stock_quantity 는 실제 스키마에서 NULL 허용입니다.
  AND COALESCE(p.stock_quantity, 0) > 0
  AND upa.last_purchased_at IS NOT NULL
  AND upa.last_purchased_at < NOW() - MAKE_INTERVAL(days => $2)
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
LIMIT $3;
