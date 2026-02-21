from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_agent_service
from app.integration.skillkit_bridge import get_bridge
from app.schemas.agent import AgentConfigUpdate, AgentListResponse, AgentSessionResponse, AgentStatusResponse
from app.services.agent_service import AgentService
from app.websocket.events import AGENT_STATUS_CHANGED
from app.websocket.manager import ws_manager

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=AgentListResponse)
async def list_agents(service: AgentService = Depends(get_agent_service)):
    agents = await service.list_agents()
    return AgentListResponse(agents=agents)


@router.get("/{role}", response_model=AgentStatusResponse)
async def get_agent(role: str, service: AgentService = Depends(get_agent_service)):
    agent = await service.get_agent(role)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{role}' not found")
    return agent


@router.put("/{role}/config", response_model=AgentStatusResponse)
async def update_agent_config(
    role: str,
    update: AgentConfigUpdate,
    service: AgentService = Depends(get_agent_service),
):
    agent = await service.update_config(role, update)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{role}' not found")
    return agent


@router.post("/{role}/start", response_model=AgentStatusResponse)
async def start_agent(role: str, service: AgentService = Depends(get_agent_service)):
    bridge = get_bridge()
    await bridge.start_agent(role)
    agent = await service.mark_running(role)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{role}' not found")
    await ws_manager.broadcast(AGENT_STATUS_CHANGED, {"role": role, "status": "running"})
    return agent


@router.post("/{role}/stop", response_model=AgentStatusResponse)
async def stop_agent(role: str, service: AgentService = Depends(get_agent_service)):
    bridge = get_bridge()
    await bridge.stop_agent(role)
    agent = await service.mark_stopped(role)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{role}' not found")
    await ws_manager.broadcast(AGENT_STATUS_CHANGED, {"role": role, "status": "idle"})
    return agent


@router.get("/{role}/session", response_model=AgentSessionResponse)
async def get_agent_session(role: str, service: AgentService = Depends(get_agent_service)):
    session = await service.get_session(role)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Agent '{role}' not found")
    return session


@router.post("/{role}/chat")
async def chat_with_agent(role: str, message: dict):
    bridge = get_bridge()
    result = await bridge.send_message(role, message.get("content", ""))
    return result
