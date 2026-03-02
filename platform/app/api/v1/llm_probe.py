from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_llm_probe_service
from app.schemas.llm_probe import LLMProbeResponse
from app.services.llm_probe_service import LLMProbeService

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/probe", response_model=LLMProbeResponse)
async def probe_llm(
    timeout_ms: int = Query(3000, ge=500, le=10000),
    service: LLMProbeService = Depends(get_llm_probe_service),
) -> LLMProbeResponse:
    """Execute a low-cost model probe and return normalized health diagnostics."""
    return await service.probe(timeout_ms=timeout_ms)
