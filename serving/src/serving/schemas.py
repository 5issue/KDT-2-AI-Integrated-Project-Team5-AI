"""API 응답 스키마 (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """liveness 응답."""

    status: str = "ok"
    version: str
    environment: str


class DbHealthResponse(BaseModel):
    """readiness 응답. 호스트나 자격증명은 담지 않습니다."""

    ok: bool
    latency_ms: float
    database: str | None = None
    server_version: str | None = None
    pool_size: int | None = None
    pool_idle: int | None = None
    detail: str | None = None
    notes: list[str] = Field(default_factory=list)


class RecipeRecommendation(BaseModel):
    """냉장고 기반 레시피 추천 한 건."""

    model_config = ConfigDict(from_attributes=True)

    recipe_id: int
    name: str
    difficulty: str | None = None
    cook_time_min: int | None = None
    required_count: int
    covered_count: int
    missing_count: int
    coverage: Decimal


class ReorderCandidate(BaseModel):
    """재구매 후보 한 건."""

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    product_name: str
    price: Decimal
    storage_type: str | None = None
    purchase_count: int
    last_purchased_at: datetime | None = None
    affinity_score: float
    days_elapsed: int


class RecommendationListResponse(BaseModel):
    """추천 목록 응답 공통 껍데기."""

    user_id: int
    count: int
    items: list[RecipeRecommendation]


class ReorderListResponse(BaseModel):
    """재구매 후보 목록 응답."""

    user_id: int
    count: int
    items: list[ReorderCandidate]
