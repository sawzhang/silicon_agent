from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


VALID_PROVIDERS = {"github", "jira", "gitlab"}


class IntegrationCreateRequest(BaseModel):
    provider: str = Field(..., description="集成提供方: github | jira | gitlab")
    access_token: Optional[str] = Field(None, description="访问令牌")
    extra_config: Optional[dict[str, Any]] = Field(None, description="额外配置")
    enabled: bool = Field(default=True, description="是否启用")


class IntegrationUpdateRequest(BaseModel):
    access_token: Optional[str] = Field(None, description="访问令牌")
    extra_config: Optional[dict[str, Any]] = Field(None, description="额外配置")
    enabled: Optional[bool] = Field(None, description="是否启用")


class IntegrationResponse(BaseModel):
    id: str
    project_id: str
    provider: str
    webhook_secret: str
    access_token: Optional[str]
    extra_config: Optional[dict[str, Any]]
    enabled: bool
    webhook_url: str = Field(description="完整的 Webhook URL")
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
