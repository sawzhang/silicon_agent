from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gate import HumanGateModel
from app.schemas.gate import GateApproveRequest, GateDetailResponse, GateListResponse, GateRejectRequest


class GateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_gates(
        self, page: int = 1, page_size: int = 20, status: Optional[str] = None
    ) -> GateListResponse:
        query = select(HumanGateModel)
        count_query = select(func.count()).select_from(HumanGateModel)

        if status:
            query = query.where(HumanGateModel.status == status)
            count_query = count_query.where(HumanGateModel.status == status)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(HumanGateModel.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        gates = result.scalars().all()

        return GateListResponse(
            items=[GateDetailResponse.model_validate(g) for g in gates],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_gate(self, gate_id: str) -> Optional[GateDetailResponse]:
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        return GateDetailResponse.model_validate(gate)

    async def approve(self, gate_id: str, request: GateApproveRequest) -> Optional[GateDetailResponse]:
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        gate.status = "approved"
        gate.reviewer = request.reviewer
        gate.review_comment = request.comment
        gate.reviewed_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(gate)
        return GateDetailResponse.model_validate(gate)

    async def reject(self, gate_id: str, request: GateRejectRequest) -> Optional[GateDetailResponse]:
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        gate.status = "rejected"
        gate.reviewer = request.reviewer
        gate.review_comment = request.comment
        gate.reviewed_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(gate)
        return GateDetailResponse.model_validate(gate)

    async def get_history(
        self, page: int = 1, page_size: int = 20
    ) -> GateListResponse:
        query = (
            select(HumanGateModel)
            .where(HumanGateModel.status.in_(["approved", "rejected"]))
            .order_by(HumanGateModel.reviewed_at.desc())
        )
        count_query = (
            select(func.count())
            .select_from(HumanGateModel)
            .where(HumanGateModel.status.in_(["approved", "rejected"]))
        )

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(query)
        gates = result.scalars().all()

        return GateListResponse(
            items=[GateDetailResponse.model_validate(g) for g in gates],
            total=total,
            page=page,
            page_size=page_size,
        )
