"""홈 엔드포인트 (명세 13장).

버블은 후보 수가 하한(min_candidates) 미달이면 enabled=false 로 내립니다.
눌렀을 때 빈 결과가 나오는 버블을 화면에서 막기 위한 것입니다.
"""

from __future__ import annotations

from fastapi import APIRouter

from serving.dependencies import PoolDep
from serving.envelope import ApiResponse
from serving.queries import build_query
from serving.schemas import BubbleItem, BubbleListResponse

router = APIRouter(prefix="/home", tags=["home"])


@router.get("/bubbles", response_model=ApiResponse[BubbleListResponse])
async def read_bubbles(pool: PoolDep) -> ApiResponse[BubbleListResponse]:
    """홈 추천 버블 목록을 냅니다."""
    sql, args = build_query("bubble_candidate_counts", {})
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = [BubbleItem.from_row(dict(row)) for row in rows]
    return ApiResponse.success(BubbleListResponse(items=items))
