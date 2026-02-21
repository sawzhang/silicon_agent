from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import CircuitBreakerModel
from app.schemas.audit import CircuitBreakerListResponse, CircuitBreakerResponse


class CircuitBreakerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_status(self) -> CircuitBreakerListResponse:
        result = await self.session.execute(
            select(CircuitBreakerModel).order_by(CircuitBreakerModel.triggered_at.desc())
        )
        items = result.scalars().all()

        return CircuitBreakerListResponse(
            items=[CircuitBreakerResponse.model_validate(cb) for cb in items],
            total=len(items),
        )

    async def trigger(
        self, level: int, triggered_by: str, reason: str
    ) -> CircuitBreakerResponse:
        cb = CircuitBreakerModel(
            level=level,
            status="triggered",
            triggered_by=triggered_by,
            trigger_reason=reason,
            triggered_at=datetime.now(timezone.utc),
        )
        self.session.add(cb)
        await self.session.commit()
        await self.session.refresh(cb)
        return CircuitBreakerResponse.model_validate(cb)

    async def resolve(
        self, cb_id: str, resolved_by: str
    ) -> Optional[CircuitBreakerResponse]:
        result = await self.session.execute(
            select(CircuitBreakerModel).where(CircuitBreakerModel.id == cb_id)
        )
        cb = result.scalar_one_or_none()
        if cb is None:
            return None
        cb.status = "resolved"
        cb.resolved_at = datetime.now(timezone.utc)
        cb.resolved_by = resolved_by
        await self.session.commit()
        await self.session.refresh(cb)
        return CircuitBreakerResponse.model_validate(cb)
