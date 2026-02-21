from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ProjectCreateRequest(BaseModel):
    name: str
    display_name: str
    repo_url: Optional[str] = None
    branch: str = "main"
    description: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    repo_url: Optional[str] = None
    branch: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    display_name: str
    repo_url: Optional[str] = None
    branch: str = "main"
    description: Optional[str] = None
    status: str = "active"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    items: List[ProjectResponse]
    total: int
