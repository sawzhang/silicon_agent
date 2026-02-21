"""Single-stage executor: build prompt -> call LLM -> update DB -> broadcast events."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import List, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.llm_client import ChatMessage, get_llm_client
from app.models.agent import AgentModel
from app.models.task import TaskModel, TaskStageModel
from app.websocket.events import AGENT_STATUS_CHANGED, TASK_STAGE_UPDATE
from app.websocket.manager import ws_manager
from app.worker.prompts import StageContext, build_messages

logger = logging.getLogger(__name__)


async def execute_stage(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    prior_outputs: List[Dict[str, str]],
) -> str:
    """Execute a single stage: call LLM and update DB/broadcast.

    Returns the LLM output text.
    """
    now = datetime.now(timezone.utc)

    # 1. Mark stage as running
    stage.status = "running"
    stage.started_at = now
    await session.commit()

    # 2. Update agent status
    agent = await _get_agent(session, stage.agent_role)
    if agent:
        agent.status = "running"
        agent.current_task_id = task.id
        agent.started_at = now
        agent.last_active_at = now
        await session.commit()
        await ws_manager.broadcast(AGENT_STATUS_CHANGED, {
            "role": agent.role,
            "status": "running",
            "current_task_id": task.id,
        })

    # 3. Broadcast stage running
    await ws_manager.broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "running",
    })

    # 4. Build prompt and call LLM
    start_time = time.monotonic()
    ctx = StageContext(
        task_title=task.title,
        task_description=task.description,
        stage_name=stage.stage_name,
        agent_role=stage.agent_role,
        prior_outputs=prior_outputs,
    )
    messages_dicts = build_messages(ctx)
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages_dicts]

    llm_client = get_llm_client()
    llm_response = await llm_client.chat(messages)
    elapsed = time.monotonic() - start_time

    # 5. Update stage as completed
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = round(elapsed, 2)
    stage.tokens_used = llm_response.total_tokens
    stage.output_summary = llm_response.content
    await session.commit()

    # 6. Update task total tokens
    task.total_tokens += llm_response.total_tokens
    await session.commit()

    # 7. Broadcast stage completed
    await ws_manager.broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "completed",
        "duration_seconds": stage.duration_seconds,
        "tokens_used": llm_response.total_tokens,
    })

    # 8. Reset agent to idle
    if agent:
        agent.status = "idle"
        agent.current_task_id = None
        agent.last_active_at = datetime.now(timezone.utc)
        await session.commit()
        await ws_manager.broadcast(AGENT_STATUS_CHANGED, {
            "role": agent.role,
            "status": "idle",
            "current_task_id": None,
        })

    logger.info(
        "Stage %s completed: %.1fs, %d tokens",
        stage.stage_name,
        elapsed,
        llm_response.total_tokens,
    )
    return llm_response.content


async def mark_stage_failed(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    error_message: str,
) -> None:
    """Mark a stage as failed and reset the agent."""
    stage.status = "failed"
    stage.error_message = error_message
    stage.completed_at = datetime.now(timezone.utc)
    await session.commit()

    # Reset agent
    agent = await _get_agent(session, stage.agent_role)
    if agent:
        agent.status = "idle"
        agent.current_task_id = None
        await session.commit()
        await ws_manager.broadcast(AGENT_STATUS_CHANGED, {
            "role": agent.role,
            "status": "idle",
            "current_task_id": None,
        })

    # Broadcast failure
    await ws_manager.broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "failed",
        "error_message": error_message,
    })

    logger.error("Stage %s failed: %s", stage.stage_name, error_message)


async def _get_agent(session: AsyncSession, role: str) -> AgentModel | None:
    result = await session.execute(
        select(AgentModel).where(AgentModel.role == role)
    )
    return result.scalar_one_or_none()
