"""3단계 파이프라인. 각 단계가 LLM Batch 로 판단하고 중간 산출물을 남깁니다."""

STAGE_PROFILE = "stage1_profile"
STAGE_EXTRACT = "stage2_extract"
STAGE_RESOLVE = "stage3_resolve"

__all__ = ["STAGE_EXTRACT", "STAGE_PROFILE", "STAGE_RESOLVE"]
