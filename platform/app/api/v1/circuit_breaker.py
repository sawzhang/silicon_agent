from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_circuit_breaker_service
from app.schemas.audit import CircuitBreakerListResponse, CircuitBreakerResponse
from app.services.circuit_breaker_service import CircuitBreakerService
from app.websocket.events import CB_RESOLVED, CB_TRIGGERED
from app.websocket.manager import ws_manager

router = APIRouter(prefix="/circuit-breaker", tags=["circuit-breaker"])


class TriggerRequest(BaseModel):
    level: int
    triggered_by: str
    reason: str


class ResolveRequest(BaseModel):
    id: str
    resolved_by: str


@router.get("", response_model=CircuitBreakerListResponse)
async def get_circuit_breaker_status(
    service: CircuitBreakerService = Depends(get_circuit_breaker_service),
):
    return await service.get_status()


@router.post("/trigger", response_model=CircuitBreakerResponse, status_code=201)
async def trigger_circuit_breaker(
    request: TriggerRequest,
    service: CircuitBreakerService = Depends(get_circuit_breaker_service),
):
    cb = await service.trigger(
        level=request.level,
        triggered_by=request.triggered_by,
        reason=request.reason,
    )
    await ws_manager.broadcast(CB_TRIGGERED, {
        "id": cb.id,
        "level": cb.level,
        "triggered_by": cb.triggered_by,
        "reason": cb.trigger_reason,
    })
    return cb


@router.post("/resolve", response_model=CircuitBreakerResponse)
async def resolve_circuit_breaker(
    request: ResolveRequest,
    service: CircuitBreakerService = Depends(get_circuit_breaker_service),
):
    cb = await service.resolve(cb_id=request.id, resolved_by=request.resolved_by)
    if cb is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Circuit breaker record not found")
    await ws_manager.broadcast(CB_RESOLVED, {
        "id": cb.id,
        "level": cb.level,
        "resolved_by": cb.resolved_by,
    })
    return cb
