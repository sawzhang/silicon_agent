from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLogModel
from app.models.kpi import KPIMetricModel

logger = logging.getLogger(__name__)


class EventCollector:
    """Collects and persists platform events for auditing and KPI tracking."""

    async def record_audit(
        self,
        session: AsyncSession,
        agent_role: str,
        action_type: str,
        detail: Optional[dict] = None,
        risk_level: str = "low",
    ) -> None:
        log = AuditLogModel(
            agent_role=agent_role,
            action_type=action_type,
            action_detail=detail,
            risk_level=risk_level,
        )
        session.add(log)
        await session.commit()
        logger.info("Audit event recorded: %s / %s", agent_role, action_type)

    async def record_metric(
        self,
        session: AsyncSession,
        metric_name: str,
        agent_role: str,
        value: float,
        unit: str = "count",
        metadata: Optional[dict] = None,
    ) -> None:
        metric = KPIMetricModel(
            metric_name=metric_name,
            agent_role=agent_role,
            value=value,
            unit=unit,
            recorded_at=datetime.now(timezone.utc),
            extra_data=metadata,
        )
        session.add(metric)
        await session.commit()
        logger.info("KPI metric recorded: %s = %s %s", metric_name, value, unit)


event_collector = EventCollector()
