from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: str
    agent_role: str
    action_type: str
    action_detail: Optional[dict] = None
    risk_level: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    items: List[AuditLogResponse]
    total: int
    page: int
    page_size: int


class CircuitBreakerResponse(BaseModel):
    id: str
    level: int
    status: str
    triggered_by: Optional[str] = None
    trigger_reason: Optional[str] = None
    triggered_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None

    model_config = {"from_attributes": True}


class CircuitBreakerListResponse(BaseModel):
    items: List[CircuitBreakerResponse]
    total: int
