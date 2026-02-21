from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class StageDefinition(BaseModel):
    name: str
    agent_role: str
    order: int


class GateDefinition(BaseModel):
    after_stage: str
    type: str = "human_approve"


class TemplateCreateRequest(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    stages: List[StageDefinition] = []
    gates: List[GateDefinition] = []


class TemplateUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    stages: Optional[List[StageDefinition]] = None
    gates: Optional[List[GateDefinition]] = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    stages: List[Any] = []
    gates: List[Any] = []
    is_builtin: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateListResponse(BaseModel):
    items: List[TemplateResponse]
    total: int
