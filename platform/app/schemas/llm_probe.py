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
