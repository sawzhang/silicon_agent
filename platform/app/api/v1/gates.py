from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_gate_service
from app.schemas.gate import GateApproveRequest, GateDetailResponse, GateListResponse, GateRejectRequest
from app.services.gate_service import GateService
from app.websocket.events import GATE_APPROVED, GATE_REJECTED
from app.websocket.manager import ws_manager

router = APIRouter(prefix="/gates", tags=["gates"])


@router.get("", response_model=GateListResponse)
async def list_gates(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    service: GateService = Depends(get_gate_service),
):
    return await service.list_gates(page=page, page_size=page_size, status=status)


@router.get("/history", response_model=GateListResponse)
async def get_gate_history(
    page: int = 1,
    page_size: int = 20,
    service: GateService = Depends(get_gate_service),
):
    return await service.get_history(page=page, page_size=page_size)


@router.get("/{gate_id}", response_model=GateDetailResponse)
async def get_gate(gate_id: str, service: GateService = Depends(get_gate_service)):
    gate = await service.get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found")
    return gate


@router.post("/{gate_id}/approve", response_model=GateDetailResponse)
async def approve_gate(
    gate_id: str,
    request: GateApproveRequest,
    service: GateService = Depends(get_gate_service),
):
    gate = await service.approve(gate_id, request)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found")
    await ws_manager.broadcast(GATE_APPROVED, {"gate_id": gate_id, "reviewer": request.reviewer})
    return gate


@router.post("/{gate_id}/reject", response_model=GateDetailResponse)
async def reject_gate(
    gate_id: str,
    request: GateRejectRequest,
    service: GateService = Depends(get_gate_service),
):
    gate = await service.reject(gate_id, request)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found")
    await ws_manager.broadcast(GATE_REJECTED, {"gate_id": gate_id, "reviewer": request.reviewer})
    return gate
