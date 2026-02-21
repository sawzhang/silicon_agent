from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_audit_service
from app.schemas.audit import AuditLogListResponse, AuditLogResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    page: int = 1,
    page_size: int = 20,
    agent_role: Optional[str] = None,
    risk_level: Optional[str] = None,
    action_type: Optional[str] = None,
    service: AuditService = Depends(get_audit_service),
):
    return await service.list_logs(
        page=page,
        page_size=page_size,
        agent_role=agent_role,
        risk_level=risk_level,
        action_type=action_type,
    )


@router.get("/logs/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(log_id: str, service: AuditService = Depends(get_audit_service)):
    log = await service.get_log(log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Audit log not found")
    return log
