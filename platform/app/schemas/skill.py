from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class SkillCreateRequest(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    layer: str = "L1"
    tags: Optional[List[str]] = None
    applicable_roles: Optional[List[str]] = None
    content: Optional[str] = None
    git_path: Optional[str] = None


class SkillUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    layer: Optional[str] = None
    tags: Optional[List[str]] = None
    applicable_roles: Optional[List[str]] = None
    content: Optional[str] = None
    git_path: Optional[str] = None
    version: Optional[str] = None


class SkillDetailResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    layer: str
    tags: Optional[List[str]] = None
    applicable_roles: Optional[List[str]] = None
    status: str
    version: str
    content: Optional[str] = None
    git_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillListResponse(BaseModel):
    items: List[SkillDetailResponse]
    total: int
    page: int
    page_size: int


class SkillStatsResponse(BaseModel):
    total: int
    by_layer: Dict[str, int]
    by_status: Dict[str, int]
