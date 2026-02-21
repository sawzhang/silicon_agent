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
