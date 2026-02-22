from __future__ import annotations

import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.dependencies import get_kpi_service
from app.schemas.kpi import (
    CockpitResponse,
    KPIReportResponse,
    KPISummaryResponse,
    KPITimeSeriesResponse,
    ROISummaryResponse,
)
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


@router.get("/report")
async def get_kpi_report(
    period: str = "daily",
    format: str = "json",
    service: KPIService = Depends(get_kpi_service),
):
    report = await service.generate_report(period=period)
    if format == "csv":
        return _report_to_csv(report)
    return report


@router.get("/roi", response_model=ROISummaryResponse)
async def get_roi_summary(
    days: int = Query(30, ge=1, le=365),
    service: KPIService = Depends(get_kpi_service),
):
    return await service.get_roi_summary(days=days)


@router.get("/cockpit", response_model=CockpitResponse)
async def get_cockpit(service: KPIService = Depends(get_kpi_service)):
    return await service.get_cockpit()


@router.get("/compare")
async def compare_kpi(
    metric_name: str,
    roles: Optional[List[str]] = Query(None),
    service: KPIService = Depends(get_kpi_service),
):
    return await service.compare(metric_name, roles=roles)


def _report_to_csv(report: KPIReportResponse) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Summary section
    writer.writerow(["KPI Report", report.period, report.generated_at.isoformat()])
    writer.writerow([])
    writer.writerow(["Metric", "Value"])
    s = report.summary
    writer.writerow(["Total Tasks", s.total_tasks])
    writer.writerow(["Completed Tasks", s.completed_tasks])
    writer.writerow(["Success Rate (%)", s.success_rate])
    writer.writerow(["Avg Duration (min)", s.avg_duration_minutes])
    writer.writerow(["Total Tokens", s.total_tokens])
    writer.writerow(["Total Cost (RMB)", s.total_cost_rmb])

    # Per-agent section
    if report.by_agent:
        writer.writerow([])
        writer.writerow(["Agent", "Stages", "Avg Duration (min)", "Tokens"])
        for role, agent_summary in report.by_agent.items():
            writer.writerow([
                role,
                agent_summary.total_tasks,
                agent_summary.avg_duration_minutes,
                agent_summary.total_tokens,
            ])

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=kpi_report_{report.period}.csv"},
    )
