from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class KPIMetricValue(BaseModel):
    metric_name: str
    value: float
    unit: str
    agent_role: Optional[str] = None
    recorded_at: Optional[datetime] = None


class KPISummaryResponse(BaseModel):
    total_tasks: int = 0
    completed_tasks: int = 0
    success_rate: float = 0.0
    avg_duration_minutes: float = 0.0
    total_tokens: int = 0
    total_cost_rmb: float = 0.0
    metrics: List[KPIMetricValue] = []


class KPITimeSeriesPoint(BaseModel):
    timestamp: datetime
    value: float


class KPITimeSeriesResponse(BaseModel):
    metric_name: str
    unit: str
    data: List[KPITimeSeriesPoint]


class KPIReportResponse(BaseModel):
    generated_at: datetime
    period: str
    summary: KPISummaryResponse
    by_agent: Dict[str, KPISummaryResponse] = {}


# ── ROI Dashboard ──────────────────────────────────────────


class ROITaskBreakdown(BaseModel):
    task_id: str
    title: str
    agent_cost_rmb: float
    estimated_manual_rmb: float
    savings_rmb: float
    agent_duration_minutes: float
    estimated_manual_hours: float


class AgentRoleEfficiency(BaseModel):
    role: str
    display_name: str
    total_stages: int
    total_tokens: int
    avg_duration_seconds: float
    total_cost_rmb: float


class ROISummaryResponse(BaseModel):
    total_tasks_completed: int
    total_agent_cost_rmb: float
    total_estimated_manual_rmb: float
    total_savings_rmb: float
    roi_ratio: float
    total_agent_hours: float
    total_estimated_manual_hours: float
    time_saved_hours: float

    benchmark_hours_per_task: float
    benchmark_hourly_rate: float

    by_role: List[AgentRoleEfficiency]
    recent_tasks: List[ROITaskBreakdown]


# ── Developer Cockpit ──────────────────────────────────────


class CockpitTaskItem(BaseModel):
    id: str
    title: str
    status: str
    project_name: Optional[str] = None
    template_name: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    current_stage: Optional[str] = None
    error_message: Optional[str] = None
    total_tokens: int
    total_cost_rmb: float

    model_config = {"from_attributes": True}


class CockpitResponse(BaseModel):
    pending_gates_count: int
    running_tasks_count: int
    failed_tasks_today: int
    completed_tasks_today: int

    pending_gates: List["GateDetailResponse"]
    running_tasks: List[CockpitTaskItem]
    failed_tasks: List[CockpitTaskItem]
    recent_completed: List[CockpitTaskItem]


# Avoid circular import – import at module level is fine because
# gate.py has no reverse dependency on kpi.py.
from app.schemas.gate import GateDetailResponse  # noqa: E402

CockpitResponse.model_rebuild()
