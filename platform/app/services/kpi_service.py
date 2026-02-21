from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kpi import KPIMetricModel
from app.models.task import TaskModel
from app.schemas.kpi import (
    KPIMetricValue,
    KPIReportResponse,
    KPISummaryResponse,
    KPITimeSeriesPoint,
    KPITimeSeriesResponse,
)


class KPIService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_summary(self) -> KPISummaryResponse:
        total_result = await self.session.execute(
            select(func.count()).select_from(TaskModel)
        )
        total_tasks = total_result.scalar() or 0

        completed_result = await self.session.execute(
            select(func.count()).select_from(TaskModel).where(TaskModel.status == "completed")
        )
        completed_tasks = completed_result.scalar() or 0

        success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0.0

        tokens_result = await self.session.execute(
            select(func.sum(TaskModel.total_tokens)).select_from(TaskModel)
        )
        total_tokens = tokens_result.scalar() or 0

        cost_result = await self.session.execute(
            select(func.sum(TaskModel.total_cost_rmb)).select_from(TaskModel)
        )
        total_cost = cost_result.scalar() or 0.0

        metrics_result = await self.session.execute(
            select(KPIMetricModel).order_by(KPIMetricModel.recorded_at.desc()).limit(20)
        )
        metrics = metrics_result.scalars().all()

        return KPISummaryResponse(
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            success_rate=round(success_rate, 2),
            avg_duration_minutes=0.0,
            total_tokens=total_tokens,
            total_cost_rmb=round(total_cost, 4),
            metrics=[
                KPIMetricValue(
                    metric_name=m.metric_name,
                    value=m.value,
                    unit=m.unit,
                    agent_role=m.agent_role,
                    recorded_at=m.recorded_at,
                )
                for m in metrics
            ],
        )

    async def get_timeseries(
        self, metric_name: str, agent_role: Optional[str] = None
    ) -> KPITimeSeriesResponse:
        query = (
            select(KPIMetricModel)
            .where(KPIMetricModel.metric_name == metric_name)
            .order_by(KPIMetricModel.recorded_at)
        )
        if agent_role:
            query = query.where(KPIMetricModel.agent_role == agent_role)

        result = await self.session.execute(query)
        metrics = result.scalars().all()

        unit = metrics[0].unit if metrics else "count"

        return KPITimeSeriesResponse(
            metric_name=metric_name,
            unit=unit,
            data=[
                KPITimeSeriesPoint(timestamp=m.recorded_at, value=m.value)
                for m in metrics
            ],
        )

    async def generate_report(self, period: str = "daily") -> KPIReportResponse:
        summary = await self.get_summary()
        return KPIReportResponse(
            generated_at=datetime.now(timezone.utc),
            period=period,
            summary=summary,
            by_agent={},
        )

    async def compare(
        self, metric_name: str, roles: Optional[List[str]] = None
    ) -> Dict[str, List[KPIMetricValue]]:
        target_roles = roles or [
            "orchestrator", "spec", "coding", "test", "review", "smoke", "doc"
        ]
        result: Dict[str, List[KPIMetricValue]] = {}
        for role in target_roles:
            query = (
                select(KPIMetricModel)
                .where(
                    KPIMetricModel.metric_name == metric_name,
                    KPIMetricModel.agent_role == role,
                )
                .order_by(KPIMetricModel.recorded_at.desc())
                .limit(10)
            )
            db_result = await self.session.execute(query)
            metrics = db_result.scalars().all()
            result[role] = [
                KPIMetricValue(
                    metric_name=m.metric_name,
                    value=m.value,
                    unit=m.unit,
                    agent_role=m.agent_role,
                    recorded_at=m.recorded_at,
                )
                for m in metrics
            ]
        return result
