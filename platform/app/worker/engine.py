"""Worker engine: poll DB for pending tasks, orchestrate stage execution, handle gates."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.session import async_session_factory
from app.integration.event_collector import event_collector
from app.models.audit import CircuitBreakerModel
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.websocket.events import CB_TRIGGERED, GATE_CREATED, TASK_STATUS_CHANGED
from app.websocket.manager import ws_manager
from app.worker.compressor import CompressionResult, compress_stage_output
from app.worker.executor import execute_stage, mark_stage_failed

logger = logging.getLogger(__name__)

_running = False
_task: Optional[asyncio.Task] = None


async def _safe_broadcast(event: str, data: dict) -> None:
    """Broadcast a WebSocket event, swallowing any errors."""
    try:
        await ws_manager.broadcast(event, data)
    except Exception:
        logger.warning("WS broadcast failed for event %s, ignoring", event, exc_info=True)


async def start_worker() -> None:
    """Start the background worker polling loop."""
    global _running, _task
    if _running:
        logger.warning("Worker already running")
        return

    # Recover tasks stuck in running/claimed from a previous crash
    await _recover_stale_tasks()

    _running = True
    _task = asyncio.create_task(_poll_loop())
    logger.info("Worker started (poll_interval=%.1fs)", settings.WORKER_POLL_INTERVAL)


async def _recover_stale_tasks() -> None:
    """Reset tasks stuck in 'running' or 'claimed' back to 'pending' on startup."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                update(TaskModel)
                .where(TaskModel.status.in_(["running", "claimed"]))
                .values(status="pending")
            )
            await session.commit()
            if result.rowcount:
                logger.warning(
                    "Recovered %d stale tasks (running/claimed → pending)",
                    result.rowcount,
                )
    except Exception:
        logger.exception("Failed to recover stale tasks")


async def stop_worker() -> None:
    """Stop the background worker and clean up agent pool."""
    global _running, _task
    _running = False
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None

    from app.worker.agents import close_all_agents
    close_all_agents()

    logger.info("Worker stopped")


async def _poll_loop() -> None:
    """Main polling loop: pick up the oldest pending task and process it."""
    while _running:
        try:
            async with async_session_factory() as session:
                task = await _pick_pending_task(session)
                if task:
                    logger.info("Worker picked up task: %s (%s)", task.title, task.id)
                    try:
                        await asyncio.wait_for(
                            _process_task(session, task),
                            timeout=settings.WORKER_TASK_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            "Task %s timed out after %.0fs",
                            task.id, settings.WORKER_TASK_TIMEOUT,
                        )
                        await _fail_task(
                            session, task,
                            f"Task timed out after {settings.WORKER_TASK_TIMEOUT:.0f}s",
                        )
                        from app.worker.agents import close_agents_for_task
                        close_agents_for_task(str(task.id))
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Worker poll loop error")

        try:
            await asyncio.sleep(settings.WORKER_POLL_INTERVAL)
        except asyncio.CancelledError:
            break


async def _pick_pending_task(session: AsyncSession) -> Optional[TaskModel]:
    """Atomically claim the oldest pending task.

    Uses a two-step approach to prevent multiple workers from picking the same task:
    1. Find oldest pending task ID
    2. Atomically UPDATE status to 'running' only if still pending
    3. Re-read with eager-loaded relationships
    """
    # Step 1: find candidate
    id_result = await session.execute(
        select(TaskModel.id)
        .where(TaskModel.status == "pending")
        .order_by(TaskModel.created_at.asc())
        .limit(1)
    )
    task_id = id_result.scalar_one_or_none()
    if task_id is None:
        return None

    # Step 2: atomic claim — only succeeds if still pending
    claim = await session.execute(
        update(TaskModel)
        .where(TaskModel.id == task_id, TaskModel.status == "pending")
        .values(status="claimed")
    )
    await session.commit()

    if claim.rowcount == 0:
        # Another worker got it first
        return None

    # Step 3: read full object with relationships
    result = await session.execute(
        select(TaskModel)
        .options(
            selectinload(TaskModel.stages),
            selectinload(TaskModel.template),
            selectinload(TaskModel.project),
        )
        .where(TaskModel.id == task_id)
    )
    return result.scalar_one_or_none()


async def _process_task(session: AsyncSession, task: TaskModel) -> None:
    """Orchestrate all stages of a task in order."""
    # Transition from claimed → running
    task.status = "running"
    await session.commit()
    await _safe_broadcast(TASK_STATUS_CHANGED, {
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
    compression = CompressionResult()

    # Load repo context from project
    repo_context: Optional[str] = None
    if task.project and task.project.repo_tree:
        repo_context = _build_repo_context(task.project)

    # Load project memory for this task's project
    project_memory_store = None
    if settings.MEMORY_ENABLED and task.project_id:
        try:
            from app.worker.memory import ProjectMemoryStore
            project_memory_store = ProjectMemoryStore(str(task.project_id))
        except Exception:
            logger.warning("Failed to init memory store for project %s", task.project_id, exc_info=True)

    for stage_index, stage in enumerate(sorted_stages):
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

        # Build project memory for the current role
        project_memory: Optional[str] = None
        if project_memory_store:
            try:
                project_memory = project_memory_store.get_memory_for_role(stage.agent_role)
            except Exception:
                logger.warning("Failed to load memory for role %s", stage.agent_role, exc_info=True)

        # Build compressed prior context via sliding window
        compressed_prior = compression.build_prior_context(stage_index)

        try:
            output = await execute_stage(
                session, task, stage, prior_outputs,
                compressed_outputs=compressed_prior if compressed_prior else None,
                project_memory=project_memory,
                repo_context=repo_context,
            )
            prior_outputs.append({"stage": stage.stage_name, "output": output})

            # Compress this stage's output for future stages
            try:
                co = await compress_stage_output(stage.stage_name, output)
                compression.add(co)
            except Exception:
                logger.warning("Compression failed for stage %s, skipping", stage.stage_name, exc_info=True)

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

            # Circuit breaker check
            if task.total_tokens > settings.CB_MAX_TOKENS_PER_TASK or \
               task.total_cost_rmb > settings.CB_MAX_COST_PER_TASK_RMB:
                reason = f"Circuit breaker: tokens={task.total_tokens}, cost=¥{task.total_cost_rmb:.2f}"
                cb = CircuitBreakerModel(
                    level=1,
                    status="triggered",
                    triggered_by=stage.agent_role,
                    trigger_reason=reason,
                    triggered_at=datetime.now(timezone.utc),
                )
                session.add(cb)
                await session.commit()
                await _safe_broadcast(CB_TRIGGERED, {
                    "task_id": task.id,
                    "reason": reason,
                    "total_tokens": task.total_tokens,
                    "total_cost_rmb": task.total_cost_rmb,
                })
                from app.worker.agents import close_agents_for_task
                close_agents_for_task(str(task.id))
                await _fail_task(session, task, reason)
                return
        except Exception as e:
            error_msg = str(e)
            logger.exception("Stage %s failed for task %s", stage.stage_name, task.id)
            await mark_stage_failed(session, task, stage, error_msg)
            from app.worker.agents import close_agents_for_task
            close_agents_for_task(str(task.id))
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

    # All stages completed — extract memories from this task
    if settings.MEMORY_ENABLED and project_memory_store and prior_outputs:
        try:
            from app.worker.memory_extractor import extract_and_store_memories
            await extract_and_store_memories(
                project_id=str(task.project_id),
                task_id=str(task.id),
                task_title=task.title,
                stage_outputs=prior_outputs,
            )
        except Exception:
            logger.warning("Memory extraction failed for task %s", task.id, exc_info=True)

    # Clean up per-task agents
    from app.worker.agents import close_agents_for_task
    close_agents_for_task(str(task.id))
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

    await _safe_broadcast(GATE_CREATED, {
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

    # Poll for gate resolution with timeout
    gate_start = datetime.now(timezone.utc)

    while _running:
        await asyncio.sleep(settings.WORKER_GATE_POLL_INTERVAL)

        # Check gate timeout
        elapsed = (datetime.now(timezone.utc) - gate_start).total_seconds()
        if elapsed > settings.WORKER_GATE_MAX_WAIT_SECONDS:
            logger.warning(
                "Gate %s timed out after %ds (max=%ds)",
                gate.id, elapsed, settings.WORKER_GATE_MAX_WAIT_SECONDS,
            )
            return False

        # Re-read gate status from DB (with error handling)
        try:
            await session.refresh(gate)
        except Exception:
            logger.warning("Failed to refresh gate %s, retrying", gate.id, exc_info=True)
            continue

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

    await _safe_broadcast(TASK_STATUS_CHANGED, {
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


def _build_repo_context(project) -> str:
    """Build a text block describing the project's repo for agent prompt injection."""
    parts = []
    if project.tech_stack:
        parts.append(f"### 技术栈\n{', '.join(project.tech_stack)}")
    if project.repo_tree:
        parts.append(f"### 目录结构\n{project.repo_tree}")
    if project.repo_url:
        branch = project.branch or "main"
        parts.append(f"### 仓库\n{project.repo_url} (branch: {branch})")
    return "\n\n".join(parts)


async def _fail_task(session: AsyncSession, task: TaskModel, reason: str) -> None:
    """Mark task as failed and broadcast."""
    task.status = "failed"
    task.completed_at = datetime.now(timezone.utc)
    await session.commit()

    await _safe_broadcast(TASK_STATUS_CHANGED, {
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
