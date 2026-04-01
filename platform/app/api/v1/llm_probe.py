from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from app.config import Settings, settings
from app.dependencies import get_llm_probe_service
from app.schemas.llm_probe import LLMConfigResponse, LLMConfigUpdateRequest, LLMProbeResponse
from app.services.llm_probe_service import LLMProbeService

router = APIRouter(prefix="/llm", tags=["llm"])


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _parse_role_model_map() -> dict[str, str]:
    try:
        parsed = json.loads(settings.LLM_ROLE_MODEL_MAP)
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


@router.get("/probe", response_model=LLMProbeResponse)
async def probe_llm(
    timeout_ms: int = Query(3000, ge=500, le=10000),
    service: LLMProbeService = Depends(get_llm_probe_service),
) -> LLMProbeResponse:
    """Execute a low-cost model probe and return normalized health diagnostics."""
    return await service.probe(timeout_ms=timeout_ms)


@router.get("/config", response_model=LLMConfigResponse)
async def get_llm_config() -> LLMConfigResponse:
    """Return current LLM connection configuration with masked API key."""
    return LLMConfigResponse(
        api_key_set=bool(settings.LLM_API_KEY),
        api_key_masked=_mask_key(settings.LLM_API_KEY),
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        timeout=settings.LLM_TIMEOUT,
        role_model_map=_parse_role_model_map(),
    )


@router.put("/config", response_model=LLMConfigResponse)
async def update_llm_config(request: LLMConfigUpdateRequest) -> LLMConfigResponse:
    """Update LLM connection settings at runtime (persists until restart)."""
    if request.api_key is not None:
        Settings.model_fields["LLM_API_KEY"].default = request.api_key
        settings.LLM_API_KEY = request.api_key
    if request.base_url is not None:
        url = request.base_url.rstrip("/")
        Settings.model_fields["LLM_BASE_URL"].default = url
        settings.LLM_BASE_URL = url
    if request.model is not None:
        Settings.model_fields["LLM_MODEL"].default = request.model
        settings.LLM_MODEL = request.model
    if request.timeout is not None:
        Settings.model_fields["LLM_TIMEOUT"].default = request.timeout
        settings.LLM_TIMEOUT = request.timeout
    return LLMConfigResponse(
        api_key_set=bool(settings.LLM_API_KEY),
        api_key_masked=_mask_key(settings.LLM_API_KEY),
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        timeout=settings.LLM_TIMEOUT,
        role_model_map=_parse_role_model_map(),
    )
