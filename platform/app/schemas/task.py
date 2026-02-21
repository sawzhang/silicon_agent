from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class TaskCreateRequest(BaseModel):
    jira_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    project_id: Optional[str] = None


class TaskStageResponse(BaseModel):
    id: str
    task_id: str
    stage_name: str
    agent_role: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    tokens_used: int = 0
    turns_used: int = 0
    self_fix_count: int = 0
    output_summary: Optional[str] = None
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}


class TaskDetailResponse(BaseModel):
    id: str
    jira_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: str
    total_tokens: int = 0
    total_cost_rmb: float = 0.0
    created_at: datetime
    completed_at: Optional[datetime] = None
    stages: List[TaskStageResponse] = []
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    template_name: Optional[str] = None
    project_name: Optional[str] = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    items: List[TaskDetailResponse]
    total: int
    page: int
    page_size: int
