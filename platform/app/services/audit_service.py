from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLogModel
from app.schemas.audit import AuditLogListResponse, AuditLogResponse


class AuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        agent_role: Optional[str] = None,
        risk_level: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> AuditLogListResponse:
        query = select(AuditLogModel)
        count_query = select(func.count()).select_from(AuditLogModel)

        if agent_role:
            query = query.where(AuditLogModel.agent_role == agent_role)
            count_query = count_query.where(AuditLogModel.agent_role == agent_role)
        if risk_level:
            query = query.where(AuditLogModel.risk_level == risk_level)
            count_query = count_query.where(AuditLogModel.risk_level == risk_level)
        if action_type:
            query = query.where(AuditLogModel.action_type == action_type)
            count_query = count_query.where(AuditLogModel.action_type == action_type)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(AuditLogModel.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        logs = result.scalars().all()

        return AuditLogListResponse(
            items=[AuditLogResponse.model_validate(log) for log in logs],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_log(self, log_id: str) -> Optional[AuditLogResponse]:
        result = await self.session.execute(
            select(AuditLogModel).where(AuditLogModel.id == log_id)
        )
        log = result.scalar_one_or_none()
        if log is None:
            return None
        return AuditLogResponse.model_validate(log)
