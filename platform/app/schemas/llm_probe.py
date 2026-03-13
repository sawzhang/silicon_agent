from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LLMProbeResponse(BaseModel):
    """Normalized payload for quick LLM liveness checks."""

    ok: bool
    provider: str
    base_url: str
    requested_model: Optional[str] = None
    resolved_model: Optional[str] = None
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    checked_at: datetime


class LLMConfigResponse(BaseModel):
    """Current LLM connection configuration (API key masked)."""

    api_key_set: bool
    api_key_masked: str
    base_url: str
    model: str
    timeout: float
    role_model_map: dict[str, str]


class LLMConfigUpdateRequest(BaseModel):
    """Request to update LLM connection settings at runtime."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout: Optional[float] = None
