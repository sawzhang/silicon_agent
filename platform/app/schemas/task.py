from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


class TaskCreateRequest(BaseModel):
    jira_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    target_branch: Optional[str] = None
    yunxiao_task_id: Optional[str] = None


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
    # Phase 1.1: Structured output
    output_structured: Optional[dict] = None
    # Phase 1.2: Failure classification
    failure_category: Optional[str] = None
    # Phase 2.2: Self-assessment confidence
    self_assessment_score: Optional[float] = None
    # Phase 2.5: Per-stage retry count
    retry_count: int = 0

    @field_validator("tokens_used", "turns_used", "self_fix_count", "retry_count", mode="before")
    @classmethod
    def _none_to_zero(cls, v: object) -> int:
        return v if v is not None else 0

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
    branch_name: Optional[str] = None
    pr_url: Optional[str] = None
    stages: List[TaskStageResponse] = []
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    template_name: Optional[str] = None
    project_name: Optional[str] = None
    target_branch: Optional[str] = None
    yunxiao_task_id: Optional[str] = None

    @field_validator("total_tokens", mode="before")
    @classmethod
    def _tokens_none(cls, v: object) -> int:
        return v if v is not None else 0

    @field_validator("total_cost_rmb", mode="before")
    @classmethod
    def _cost_none(cls, v: object) -> float:
        return v if v is not None else 0.0

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    items: List[TaskDetailResponse]
    total: int
    page: int
    page_size: int


# --- PRD Decompose ---

class TaskDecomposeRequest(BaseModel):
    prd_text: str
    project_id: Optional[str] = None
    template_id: Optional[str] = None


class DecomposedTask(BaseModel):
    title: str
    description: str
    priority: str = "medium"


class TaskDecomposeResponse(BaseModel):
    tasks: List[DecomposedTask]
    summary: str
    tokens_used: int = 0


# --- Batch Create ---

class BatchTaskItem(BaseModel):
    title: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    target_branch: Optional[str] = None
    yunxiao_task_id: Optional[str] = None


class TaskBatchCreateRequest(BaseModel):
    tasks: List[BatchTaskItem]


class TaskBatchCreateResponse(BaseModel):
    created: int
    tasks: List[TaskDetailResponse]


class TaskRetryFromStageRequest(BaseModel):
    """Request payload for retrying a task from a specific failed stage."""

    stage_id: str


class TaskBatchRetryRequest(BaseModel):
    """Request payload for retrying multiple tasks."""

    task_ids: List[str]


class TaskBatchRetryItem(BaseModel):
    """Per-task retry result for batch retry operations."""

    task_id: str
    success: bool
    reason: Optional[str] = None
    task: Optional[TaskDetailResponse] = None


class TaskBatchRetryResponse(BaseModel):
    """Aggregated response for batch retry operations."""

    total: int
    succeeded: int
    failed: int
    items: List[TaskBatchRetryItem]
