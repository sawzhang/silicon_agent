from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


class TaskLogResponse(BaseModel):
    id: str
    task_id: str
    stage_id: Optional[str] = None
    stage_name: str
    agent_role: Optional[str] = None
    correlation_id: Optional[str] = None
    event_seq: int

    event_type: str
    event_source: str
    status: str

    request_body: Optional[dict[str, Any]] = None
    response_body: Optional[dict[str, Any]] = None
    command: Optional[str] = None
    command_args: Optional[dict[str, Any]] = None
    workspace: Optional[str] = None
    execution_mode: Optional[str] = None
    duration_ms: Optional[float] = None
    result: Optional[str] = None
    output_summary: Optional[str] = None
    output_truncated: bool = False
    missing_fields: List[str] = Field(default_factory=list)

    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("missing_fields", mode="before")
    @classmethod
    def _normalize_missing_fields(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return list(value)


class TaskLogListResponse(BaseModel):
    items: List[TaskLogResponse]
    total: int
    page: int
    page_size: int
