from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class StageDefinition(BaseModel):
    name: str
    agent_role: str
    order: int
    # Phase 1.4: Enhanced stage definition fields
    model: Optional[str] = None
    instruction: Optional[str] = None
    max_turns: Optional[int] = None
    timeout: Optional[float] = None
    # Phase 1.5: Cross-stage context recall
    context_from: Optional[List[str]] = None
    # Phase 2.1: Conditional execution
    condition: Optional[dict] = None
    # Phase 2.2: Evaluator-optimizer loop
    evaluator: Optional[dict] = None
    # Phase 2.5: Per-stage retry limits
    max_retries: Optional[int] = None
    # Phase 3.1: Graph-based execution
    depends_on: Optional[List[str]] = None
    on_failure: Optional[str] = None
    max_executions: int = 1
    # Harness: verify commands for verify stages
    verify_commands: Optional[List[str]] = None
    # Phase 3.3: Dynamic routing
    routing: Optional[dict] = None


class GateDefinition(BaseModel):
    after_stage: str
    type: str = "human_approve"
    # Phase 1.3: Gate rejection feedback
    max_retries: int = 0


class TemplateCreateRequest(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    stages: List[StageDefinition] = []
    gates: List[GateDefinition] = []
    estimated_hours: Optional[float] = None


class TemplateUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    stages: Optional[List[StageDefinition]] = None
    gates: Optional[List[GateDefinition]] = None
    estimated_hours: Optional[float] = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    stages: List[Any] = []
    gates: List[Any] = []
    estimated_hours: Optional[float] = None
    is_builtin: bool = False
    # Phase 3.4: Template versioning
    version: int = 1
    parent_id: Optional[str] = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateListResponse(BaseModel):
    items: List[TemplateResponse]
    total: int


class TemplateVersionListResponse(BaseModel):
    items: List[TemplateResponse]
    total: int
