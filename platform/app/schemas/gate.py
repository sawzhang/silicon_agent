from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class GateDetailResponse(BaseModel):
    id: str
    gate_type: str
    task_id: str
    agent_role: str
    content: Optional[dict] = None
    status: str
    reviewer: Optional[str] = None
    review_comment: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class GateListResponse(BaseModel):
    items: List[GateDetailResponse]
    total: int
    page: int
    page_size: int


class GateApproveRequest(BaseModel):
    reviewer: str = "system"
    comment: Optional[str] = None


class GateRejectRequest(BaseModel):
    reviewer: str = "system"
    comment: str
