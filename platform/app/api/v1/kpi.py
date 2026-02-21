from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_kpi_service
from app.schemas.kpi import KPIReportResponse, KPISummaryResponse, KPITimeSeriesResponse
from app.services.kpi_service import KPIService

router = APIRouter(prefix="/kpi", tags=["kpi"])


@router.get("/summary", response_model=KPISummaryResponse)
async def get_kpi_summary(service: KPIService = Depends(get_kpi_service)):
    return await service.get_summary()


@router.get("/metrics/{name}", response_model=KPITimeSeriesResponse)
async def get_kpi_metric(
    name: str,
    agent_role: Optional[str] = None,
    service: KPIService = Depends(get_kpi_service),
):
    return await service.get_timeseries(name, agent_role=agent_role)


@router.get("/report", response_model=KPIReportResponse)
async def get_kpi_report(
    period: str = "daily",
    service: KPIService = Depends(get_kpi_service),
):
    return await service.generate_report(period=period)


@router.get("/compare")
async def compare_kpi(
    metric_name: str,
    roles: Optional[List[str]] = Query(None),
    service: KPIService = Depends(get_kpi_service),
):
    return await service.compare(metric_name, roles=roles)
