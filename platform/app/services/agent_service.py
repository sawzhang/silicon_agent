from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentModel
from app.schemas.agent import AgentConfigUpdate, AgentSessionResponse, AgentStatusResponse, TokenUsage

AGENT_ROLES = [
    ("orchestrator", "Orchestrator Agent"),
    ("spec", "Spec Agent"),
    ("coding", "Coding Agent"),
    ("test", "Test Agent"),
    ("review", "Review Agent"),
    ("smoke", "Smoke Test Agent"),
    ("doc", "Documentation Agent"),
]


class AgentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_agents_exist(self) -> None:
        for role, display_name in AGENT_ROLES:
            result = await self.session.execute(
                select(AgentModel).where(AgentModel.role == role)
            )
            if result.scalar_one_or_none() is None:
                agent = AgentModel(role=role, display_name=display_name, status="idle")
                self.session.add(agent)
        await self.session.commit()

    async def list_agents(self) -> List[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).order_by(AgentModel.role)
        )
        agents = result.scalars().all()
        return [AgentStatusResponse.model_validate(a) for a in agents]

    async def get_agent(self, role: str) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return AgentStatusResponse.model_validate(agent)

    async def update_config(self, role: str, update: AgentConfigUpdate) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        resolved_model = update.get_model_name()
        if resolved_model is not None:
            agent.model_name = resolved_model
        if update.config is not None:
            agent.config = update.config
        # Merge temperature/max_tokens into config JSON
        extra = {}
        if update.temperature is not None:
            extra["temperature"] = update.temperature
        if update.max_tokens is not None:
            extra["max_tokens"] = update.max_tokens
        if extra:
            current = agent.config or {}
            current.update(extra)
            agent.config = current
        await self.session.commit()
        await self.session.refresh(agent)
        return AgentStatusResponse.model_validate(agent)

    async def mark_running(self, role: str) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        agent.status = "running"
        agent.started_at = datetime.now(timezone.utc)
        agent.last_active_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(agent)
        return AgentStatusResponse.model_validate(agent)

    async def mark_stopped(self, role: str) -> Optional[AgentStatusResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        agent.status = "idle"
        agent.current_task_id = None
        await self.session.commit()
        await self.session.refresh(agent)
        return AgentStatusResponse.model_validate(agent)

    async def get_session(self, role: str) -> Optional[AgentSessionResponse]:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.role == role)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None

        uptime = None
        if agent.status == "running" and agent.started_at:
            delta = datetime.now(timezone.utc) - agent.started_at.replace(tzinfo=timezone.utc)
            uptime = delta.total_seconds()

        return AgentSessionResponse(
            role=agent.role,
            status=agent.status,
            current_task_id=agent.current_task_id,
            uptime_seconds=uptime,
            token_usage=TokenUsage(),
            turns=0,
        )
