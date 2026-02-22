from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_rmb: float = 0.0


class AgentStatusResponse(BaseModel):
    id: str
    role: str
    display_name: str
    status: str
    model_name: Optional[str] = None
    config: Optional[dict] = None
    current_task_id: Optional[str] = None
    started_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentListResponse(BaseModel):
    agents: List[AgentStatusResponse]


class AgentConfigUpdate(BaseModel):
    model_name: Optional[str] = None
    config: Optional[dict] = None
    # Accept frontend 'model' field as alias for model_name
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    enabled: Optional[bool] = None

    def get_model_name(self) -> Optional[str]:
        """Return model_name, falling back to model alias."""
        return self.model_name or self.model


class AgentSessionResponse(BaseModel):
    role: str
    status: str
    current_task_id: Optional[str] = None
    uptime_seconds: Optional[float] = None
    token_usage: TokenUsage = TokenUsage()
    turns: int = 0
