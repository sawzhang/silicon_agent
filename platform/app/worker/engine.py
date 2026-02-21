"""Worker engine: poll DB for pending tasks, orchestrate stage execution, handle gates."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.session import async_session_factory
from app.integration.event_collector import event_collector
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.websocket.events import GATE_CREATED, TASK_STATUS_CHANGED
from app.websocket.manager import ws_manager
from app.worker.executor import execute_stage, mark_stage_failed

logger = logging.getLogger(__name__)

_running = False
_task: Optional[asyncio.Task] = None


async def start_worker() -> None:
    """Start the background worker polling loop."""
    global _running, _task
    if _running:
        logger.warning("Worker already running")
        return
    _running = True
    _task = asyncio.create_task(_poll_loop())
    logger.info("Worker started (poll_interval=%.1fs)", settings.WORKER_POLL_INTERVAL)


async def stop_worker() -> None:
    """Stop the background worker."""
    global _running, _task
    _running = False
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("Worker stopped")


async def _poll_loop() -> None:
    """Main polling loop: pick up the oldest pending task and process it."""
    while _running:
        try:
            async with async_session_factory() as session:
                task = await _pick_pending_task(session)
                if task:
                    logger.info("Worker picked up task: %s (%s)", task.title, task.id)
                    await _process_task(session, task)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Worker poll loop error")

        try:
            await asyncio.sleep(settings.WORKER_POLL_INTERVAL)
        except asyncio.CancelledError:
            break


async def _pick_pending_task(session: AsyncSession) -> Optional[TaskModel]:
    """Find the oldest pending task with stages."""
    result = await session.execute(
        select(TaskModel)
        .options(
            selectinload(TaskModel.stages),
            selectinload(TaskModel.template),
        )
        .where(TaskModel.status == "pending")
        .order_by(TaskModel.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _process_task(session: AsyncSession, task: TaskModel) -> None:
    """Orchestrate all stages of a task in order."""
    # Mark task as running
    task.status = "running"
    await session.commit()
    await ws_manager.broadcast(TASK_STATUS_CHANGED, {
        "task_id": task.id,
        "status": "running",
    })

    await event_collector.record_audit(
        session,
        agent_role="orchestrator",
        action_type="task_started",
        detail={"task_id": task.id, "title": task.title},
    )

    # Parse gate definitions from template
    gates = _parse_gates(task)
    # Sort stages by template order
    sorted_stages = _sort_stages(task)

    if not sorted_stages:
        logger.warning("Task %s has no stages, marking completed", task.id)
        await _complete_task(session, task)
        return

    prior_outputs: List[Dict[str, str]] = []

    for stage in sorted_stages:
        # Check cancellation before each stage
        if await _is_cancelled(session, task.id):
            logger.info("Task %s cancelled, stopping execution", task.id)
            await event_collector.record_audit(
                session,
                agent_role="orchestrator",
                action_type="task_cancelled",
                detail={"task_id": task.id, "at_stage": stage.stage_name},
            )
            return

        try:
            output = await execute_stage(session, task, stage, prior_outputs)
            prior_outputs.append({"stage": stage.stage_name, "output": output})

            await event_collector.record_audit(
                session,
                agent_role=stage.agent_role,
                action_type=f"stage_{stage.stage_name}_completed",
                detail={
                    "task_id": task.id,
                    "stage_id": stage.id,
                    "tokens_used": stage.tokens_used,
                    "duration_seconds": stage.duration_seconds,
                },
            )
        except Exception as e:
            error_msg = str(e)
            logger.exception("Stage %s failed for task %s", stage.stage_name, task.id)
            await mark_stage_failed(session, task, stage, error_msg)
            await _fail_task(session, task, f"Stage {stage.stage_name} failed: {error_msg}")
            return

        # Check if this stage has a gate after it
        gate_type = gates.get(stage.stage_name)
        if gate_type:
            approved = await _handle_gate(
                session, task, stage, gate_type, output
            )
            if not approved:
                logger.info(
                    "Gate rejected after stage %s, failing task %s",
                    stage.stage_name,
                    task.id,
                )
                await _fail_task(
                    session, task,
                    f"Gate rejected after stage {stage.stage_name}",
                )
                return

    # All stages completed
    await _complete_task(session, task)


async def _handle_gate(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    gate_type: str,
    stage_output: str,
) -> bool:
    """Create a gate and poll until it is approved or rejected.

    Returns True if approved, False if rejected.
    """
    gate = HumanGateModel(
        gate_type=gate_type,
        task_id=task.id,
        agent_role=stage.agent_role,
        status="pending",
        content={
            "stage": stage.stage_name,
            "summary": stage_output[:500] if stage_output else "",
        },
    )
    session.add(gate)
    await session.commit()
    await session.refresh(gate)

    await ws_manager.broadcast(GATE_CREATED, {
        "gate_id": gate.id,
        "task_id": task.id,
        "gate_type": gate_type,
        "stage_name": stage.stage_name,
    })

    logger.info(
        "Gate created after stage %s (gate_id=%s), waiting for approval",
        stage.stage_name,
        gate.id,
    )

    # Poll for gate resolution
    while _running:
        await asyncio.sleep(settings.WORKER_GATE_POLL_INTERVAL)

        # Re-read gate status from DB
        await session.refresh(gate)

        if gate.status == "approved":
            logger.info("Gate %s approved", gate.id)
            return True
        elif gate.status == "rejected":
            logger.info("Gate %s rejected", gate.id)
            return False

        # Also check for task cancellation during gate wait
        if await _is_cancelled(session, task.id):
            logger.info("Task %s cancelled while waiting for gate", task.id)
            return False

    # Worker shutting down
    return False


async def _is_cancelled(session: AsyncSession, task_id: str) -> bool:
    """Re-read task status from DB to check for cancellation."""
    result = await session.execute(
        select(TaskModel.status).where(TaskModel.id == task_id)
    )
    status = result.scalar_one_or_none()
    return status == "cancelled"


def _parse_gates(task: TaskModel) -> Dict[str, str]:
    """Parse gate definitions from template.gates JSON.

    Returns {after_stage_name: gate_type}.
    """
    if not task.template:
        return {}
    try:
        gates_json = json.loads(task.template.gates) if task.template.gates else []
    except (json.JSONDecodeError, TypeError):
        return {}

    result = {}
    for g in gates_json:
        after_stage = g.get("after_stage")
        gate_type = g.get("type", "human_approve")
        if after_stage:
            result[after_stage] = gate_type
    return result


def _sort_stages(task: TaskModel) -> List[TaskStageModel]:
    """Sort stages by the order defined in template stages JSON."""
    if not task.template:
        return list(task.stages)

    try:
        stage_defs = json.loads(task.template.stages) if task.template.stages else []
    except (json.JSONDecodeError, TypeError):
        return list(task.stages)

    # Build order map: stage_name -> order
    order_map = {sd["name"]: sd.get("order", i) for i, sd in enumerate(stage_defs)}

    return sorted(task.stages, key=lambda s: order_map.get(s.stage_name, 999))


async def _complete_task(session: AsyncSession, task: TaskModel) -> None:
    """Mark task as completed and broadcast."""
    task.status = "completed"
    task.completed_at = datetime.now(timezone.utc)
    await session.commit()

    await ws_manager.broadcast(TASK_STATUS_CHANGED, {
        "task_id": task.id,
        "status": "completed",
    })

    await event_collector.record_audit(
        session,
        agent_role="orchestrator",
        action_type="task_completed",
        detail={
            "task_id": task.id,
            "title": task.title,
            "total_tokens": task.total_tokens,
        },
    )

    logger.info("Task %s completed (tokens=%d)", task.id, task.total_tokens)


async def _fail_task(session: AsyncSession, task: TaskModel, reason: str) -> None:
    """Mark task as failed and broadcast."""
    task.status = "failed"
    task.completed_at = datetime.now(timezone.utc)
    await session.commit()

    await ws_manager.broadcast(TASK_STATUS_CHANGED, {
        "task_id": task.id,
        "status": "failed",
        "reason": reason,
    })

    await event_collector.record_audit(
        session,
        agent_role="orchestrator",
        action_type="task_failed",
        detail={"task_id": task.id, "reason": reason},
        risk_level="high",
    )

    logger.error("Task %s failed: %s", task.id, reason)
