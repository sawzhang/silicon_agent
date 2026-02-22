from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.gate import HumanGateModel
from app.models.kpi import KPIMetricModel
from app.models.task import TaskModel, TaskStageModel
from app.schemas.gate import GateDetailResponse
from app.schemas.kpi import (
    AgentRoleEfficiency,
    CockpitResponse,
    CockpitTaskItem,
    KPIMetricValue,
    KPIReportResponse,
    ROISummaryResponse,
    ROITaskBreakdown,
    KPISummaryResponse,
    KPITimeSeriesPoint,
    KPITimeSeriesResponse,
)

AGENT_ROLE_NAMES = {
    "orchestrator": "Orchestrator",
    "spec": "Spec Agent",
    "coding": "Coding Agent",
    "test": "Test Agent",
    "review": "Review Agent",
    "smoke": "Smoke Test",
    "doc": "Doc Agent",
}


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

        # Compute average duration from completed tasks with both timestamps
        avg_dur_result = await self.session.execute(
            select(
                func.avg(
                    func.julianday(TaskModel.completed_at) - func.julianday(TaskModel.created_at)
                )
            )
            .select_from(TaskModel)
            .where(
                TaskModel.status == "completed",
                TaskModel.completed_at.isnot(None),
            )
        )
        avg_days = avg_dur_result.scalar() or 0.0
        avg_duration_minutes = round(avg_days * 24 * 60, 2)

        return KPISummaryResponse(
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            success_rate=round(success_rate, 2),
            avg_duration_minutes=avg_duration_minutes,
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

        # Build per-agent stats from task stages
        roles = ["orchestrator", "spec", "coding", "test", "review", "smoke", "doc"]
        by_agent: Dict[str, KPISummaryResponse] = {}

        for role in roles:
            stage_result = await self.session.execute(
                select(
                    func.count(),
                    func.sum(TaskStageModel.tokens_used),
                    func.avg(TaskStageModel.duration_seconds),
                )
                .select_from(TaskStageModel)
                .where(TaskStageModel.agent_role == role)
            )
            row = stage_result.one()
            total_stages = row[0] or 0
            role_tokens = row[1] or 0
            avg_secs = row[2] or 0.0

            by_agent[role] = KPISummaryResponse(
                total_tasks=total_stages,
                completed_tasks=0,
                success_rate=0.0,
                avg_duration_minutes=round(avg_secs / 60, 2),
                total_tokens=role_tokens,
                total_cost_rmb=0.0,
            )

        return KPIReportResponse(
            generated_at=datetime.now(timezone.utc),
            period=period,
            summary=summary,
            by_agent=by_agent,
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

    # ── ROI Dashboard ──────────────────────────────────────

    async def get_roi_summary(self, days: int = 30) -> ROISummaryResponse:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await self.session.execute(
            select(TaskModel)
            .where(
                TaskModel.status == "completed",
                TaskModel.completed_at >= cutoff,
            )
            .order_by(TaskModel.completed_at.desc())
        )
        tasks = result.scalars().all()

        hours_per_task = settings.ESTIMATED_HOURS_PER_TASK
        hourly_rate = settings.HOURLY_RATE_RMB
        estimated_manual_per_task = hours_per_task * hourly_rate

        recent_tasks: list[ROITaskBreakdown] = []
        total_agent_cost = 0.0
        total_agent_minutes = 0.0

        for t in tasks:
            agent_cost = t.total_cost_rmb or 0.0
            if t.completed_at and t.created_at:
                duration_min = (t.completed_at - t.created_at).total_seconds() / 60
            else:
                duration_min = 0.0
            savings = estimated_manual_per_task - agent_cost
            total_agent_cost += agent_cost
            total_agent_minutes += duration_min

            if len(recent_tasks) < 20:
                recent_tasks.append(
                    ROITaskBreakdown(
                        task_id=t.id,
                        title=t.title,
                        agent_cost_rmb=round(agent_cost, 4),
                        estimated_manual_rmb=round(estimated_manual_per_task, 2),
                        savings_rmb=round(savings, 2),
                        agent_duration_minutes=round(duration_min, 2),
                        estimated_manual_hours=hours_per_task,
                    )
                )

        total_completed = len(tasks)
        total_estimated_manual = estimated_manual_per_task * total_completed
        total_savings = total_estimated_manual - total_agent_cost
        roi_ratio = (total_savings / total_agent_cost) if total_agent_cost > 0 else 0.0
        total_agent_hours = total_agent_minutes / 60
        total_estimated_manual_hours = hours_per_task * total_completed
        time_saved = total_estimated_manual_hours - total_agent_hours

        # Per-role efficiency
        role_result = await self.session.execute(
            select(
                TaskStageModel.agent_role,
                func.count().label("total_stages"),
                func.sum(TaskStageModel.tokens_used).label("total_tokens"),
                func.avg(TaskStageModel.duration_seconds).label("avg_duration"),
            )
            .join(TaskModel, TaskStageModel.task_id == TaskModel.id)
            .where(
                TaskModel.status == "completed",
                TaskModel.completed_at >= cutoff,
            )
            .group_by(TaskStageModel.agent_role)
        )
        by_role: list[AgentRoleEfficiency] = []
        token_price = settings.CB_TOKEN_PRICE_PER_1K
        for row in role_result.all():
            role = row.agent_role
            tokens = row.total_tokens or 0
            by_role.append(
                AgentRoleEfficiency(
                    role=role,
                    display_name=AGENT_ROLE_NAMES.get(role, role),
                    total_stages=row.total_stages or 0,
                    total_tokens=tokens,
                    avg_duration_seconds=round(row.avg_duration or 0.0, 2),
                    total_cost_rmb=round(tokens / 1000 * token_price, 4),
                )
            )

        return ROISummaryResponse(
            total_tasks_completed=total_completed,
            total_agent_cost_rmb=round(total_agent_cost, 4),
            total_estimated_manual_rmb=round(total_estimated_manual, 2),
            total_savings_rmb=round(total_savings, 2),
            roi_ratio=round(roi_ratio, 2),
            total_agent_hours=round(total_agent_hours, 2),
            total_estimated_manual_hours=round(total_estimated_manual_hours, 2),
            time_saved_hours=round(time_saved, 2),
            benchmark_hours_per_task=hours_per_task,
            benchmark_hourly_rate=hourly_rate,
            by_role=by_role,
            recent_tasks=recent_tasks,
        )

    # ── Developer Cockpit ──────────────────────────────────

    async def get_cockpit(self) -> CockpitResponse:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Pending gates
        gate_result = await self.session.execute(
            select(HumanGateModel)
            .where(HumanGateModel.status == "pending")
            .order_by(HumanGateModel.created_at.asc())
        )
        pending_gates_models = gate_result.scalars().all()
        pending_gates = [
            GateDetailResponse.model_validate(g) for g in pending_gates_models
        ]

        # Running tasks
        running_result = await self.session.execute(
            select(TaskModel)
            .options(selectinload(TaskModel.stages))
            .where(TaskModel.status == "running")
            .order_by(TaskModel.created_at.desc())
        )
        running_models = running_result.scalars().all()
        running_tasks = [self._task_to_cockpit_item(t) for t in running_models]

        # Failed tasks today
        failed_result = await self.session.execute(
            select(TaskModel)
            .options(selectinload(TaskModel.stages))
            .where(
                TaskModel.status == "failed",
                TaskModel.completed_at >= today_start,
            )
            .order_by(TaskModel.completed_at.desc())
        )
        failed_models = failed_result.scalars().all()
        failed_tasks = [self._task_to_cockpit_item(t) for t in failed_models]

        # Recent completed
        completed_result = await self.session.execute(
            select(TaskModel)
            .options(selectinload(TaskModel.stages))
            .where(TaskModel.status == "completed")
            .order_by(TaskModel.completed_at.desc())
            .limit(10)
        )
        completed_models = completed_result.scalars().all()
        recent_completed = [self._task_to_cockpit_item(t) for t in completed_models]

        # Completed today count
        completed_today_result = await self.session.execute(
            select(func.count())
            .select_from(TaskModel)
            .where(
                TaskModel.status == "completed",
                TaskModel.completed_at >= today_start,
            )
        )
        completed_today = completed_today_result.scalar() or 0

        return CockpitResponse(
            pending_gates_count=len(pending_gates),
            running_tasks_count=len(running_tasks),
            failed_tasks_today=len(failed_tasks),
            completed_tasks_today=completed_today,
            pending_gates=pending_gates,
            running_tasks=running_tasks,
            failed_tasks=failed_tasks,
            recent_completed=recent_completed,
        )

    @staticmethod
    def _task_to_cockpit_item(task: TaskModel) -> CockpitTaskItem:
        current_stage = None
        error_message = None
        for s in (task.stages or []):
            if s.status == "running":
                current_stage = s.stage_name
            if s.status == "failed" and s.error_message:
                error_message = s.error_message
        return CockpitTaskItem(
            id=task.id,
            title=task.title,
            status=task.status,
            project_name=task.project.name if task.project else None,
            template_name=task.template.name if task.template else None,
            created_at=task.created_at,
            completed_at=task.completed_at,
            current_stage=current_stage,
            error_message=error_message,
            total_tokens=task.total_tokens or 0,
            total_cost_rmb=task.total_cost_rmb or 0.0,
        )
