from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str  # user, assistant, system
    content: str


class ChatSessionCreate(BaseModel):
    title: str
    project_id: Optional[str] = None


class ChatSessionMessageRequest(BaseModel):
    message: str


class ChatSessionApproveRequest(BaseModel):
    """
    Approve the plan and convert to a Task. 
    Optionally passing an overriding final plan or just letting the backend pull it from the session state.
    """
    template_id: Optional[str] = None
    target_branch: Optional[str] = None


class ChatSessionResponse(BaseModel):
    id: str
    title: str
    status: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    plan: Optional[dict] = None
    messages: Optional[List[ChatMessage]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionListResponse(BaseModel):
    items: List[ChatSessionResponse]
    total: int
    page: int
    page_size: int
