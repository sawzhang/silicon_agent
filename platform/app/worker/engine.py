"""Worker engine: poll DB for pending tasks, orchestrate stage execution, handle gates."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.session import async_session_factory
from app.integration.event_collector import event_collector
from app.integration.notifier import (
    notify_gate_created,
    notify_task_completed,
    notify_task_failed,
)
from app.models.audit import CircuitBreakerModel
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.websocket.events import CB_TRIGGERED, GATE_CREATED, TASK_STATUS_CHANGED
from app.websocket.manager import ws_manager
from app.services.task_log_pipeline import get_task_log_pipeline
from app.worker.compressor import CompressionResult, compress_stage_output
from app.worker.executor import execute_stage, execute_stage_sandboxed, mark_stage_failed
from app.worker.worktree import get_worktree_manager

logger = logging.getLogger(__name__)

_running = False
_task: Optional[asyncio.Task] = None


async def _safe_broadcast(event: str, data: dict) -> None:
    """Broadcast a WebSocket event, swallowing any errors."""
    try:
        await ws_manager.broadcast(event, data)
    except Exception:
        logger.warning("WS broadcast failed for event %s, ignoring", event, exc_info=True)


async def _emit_system_log(
    task: TaskModel,
    *,
    stage: TaskStageModel | None = None,
    event_type: str,
    status: str = "success",
    response_body: Optional[dict] = None,
    duration_ms: Optional[float] = None,
    result: Optional[str] = None,
    correlation_id: Optional[str] = None,
    priority: str = "normal",
) -> str:
    pipeline = get_task_log_pipeline()
    return await pipeline.emit_create(
        task_id=str(task.id),
        stage_id=str(stage.id) if stage else None,
        stage_name=stage.stage_name if stage else "task_orchestrator",
        agent_role=stage.agent_role if stage else "orchestrator",
        event_type=event_type,
        event_source="system",
        status=status,
        response_body=response_body,
        duration_ms=duration_ms,
        result=result,
        correlation_id=correlation_id,
        priority=priority,  # type: ignore[arg-type]
    )


async def _close_started_system_log(
    *,
    started_log_id: Optional[str],
    started_at_monotonic: float,
    status: str,
    result: Optional[str] = None,
) -> None:
    if not started_log_id:
        return
    duration_ms = round((time.monotonic() - started_at_monotonic) * 1000, 2)
    pipeline = get_task_log_pipeline()
    await pipeline.emit_update(
        log_id=started_log_id,
        updates={
            "status": status,
            "duration_ms": duration_ms,
            "result": result,
        },
        priority="high",
    )


async def start_worker() -> None:
    """Start the background worker polling loop."""
    global _running, _task
    if _running:
        logger.warning("Worker already running")
        return

    # Recover tasks stuck in running/claimed from a previous crash
    await _recover_stale_tasks()

    # Prune orphaned worktrees left over from previous crashes
    await _prune_stale_worktrees()

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


async def _prune_stale_worktrees() -> None:
    """Prune orphaned worktrees on startup (leftover from crashes/restarts)."""
    if not settings.WORKTREE_ENABLED:
        return
    try:
        async with async_session_factory() as session:
            # Find all projects with repo_local_path to know which repos to prune
            from app.models.project import ProjectModel
            result = await session.execute(
                select(ProjectModel.repo_local_path)
                .where(ProjectModel.repo_local_path.isnot(None))
            )
            repo_paths = [r for (r,) in result.all() if r]

        for repo_path in repo_paths:
            mgr = get_worktree_manager(repo_path)
            cleaned = await mgr.prune_all_stale()
            if cleaned:
                logger.info("Pruned %d orphan worktrees for repo %s", cleaned, repo_path)
    except Exception:
        logger.warning("Failed to prune stale worktrees on startup", exc_info=True)


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
    """Orchestrate all stages of a task in order, with parallel execution support."""
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

    # Parse gate definitions and stage metadata from template
    gates = _parse_gates(task)
    sorted_stages = _sort_stages(task)
    stage_defs = _parse_stage_defs(task)

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

    # Setup git worktree for code-producing tasks
    worktree_path: Optional[str] = None
    worktree_mgr = None
    if (
        settings.WORKTREE_ENABLED
        and task.project
        and task.project.repo_local_path
    ):
        worktree_corr = f"worktree-create-{uuid.uuid4().hex}"
        worktree_started_at = time.monotonic()
        worktree_started_log_id = await _emit_system_log(
            task,
            event_type="worktree_create_started",
            status="running",
            correlation_id=worktree_corr,
            response_body={"repo_local_path": task.project.repo_local_path},
        )
        try:
            worktree_mgr = get_worktree_manager(task.project.repo_local_path)
            worktree_path = await worktree_mgr.create_worktree(
                task_id=str(task.id),
                task_title=task.title,
                base_branch=task.project.branch or "main",
            )
            if worktree_path:
                logger.info("Task %s using worktree: %s", task.id, worktree_path)
            duration_ms = round((time.monotonic() - worktree_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_create_finished",
                status="success",
                correlation_id=worktree_corr,
                response_body={"worktree_path": worktree_path},
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=worktree_started_log_id,
                started_at_monotonic=worktree_started_at,
                status="success",
            )
        except Exception:
            logger.warning(
                "Failed to create worktree for task %s, falling back to tmpdir",
                task.id, exc_info=True,
            )
            duration_ms = round((time.monotonic() - worktree_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_create_finished",
                status="failed",
                correlation_id=worktree_corr,
                response_body={"error": "create_worktree_failed"},
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=worktree_started_log_id,
                started_at_monotonic=worktree_started_at,
                status="failed",
                result="create_worktree_failed",
            )

    # Setup container sandbox (方式1: 整体容器化)
    sandbox_info = None
    sandbox_mgr = None
    if settings.SANDBOX_ENABLED:
        from app.worker.sandbox import get_sandbox_manager
        sandbox_mgr = get_sandbox_manager()
        sandbox_image = None
        if task.project and task.project.sandbox_image:
            sandbox_image = task.project.sandbox_image
        try:
            sandbox_info = await sandbox_mgr.create(
                str(task.id),
                worktree_path=worktree_path,
                tmpdir=str(
                    __import__("pathlib").Path(__import__("tempfile").gettempdir())
                    / "silicon_agent" / "tasks" / str(task.id)
                ),
                image=sandbox_image,
            )
            if sandbox_info:
                logger.info("Task %s using sandbox container: %s", task.id, sandbox_info.container_name)
            else:
                logger.warning("Sandbox creation failed for task %s, falling back to in-process", task.id)
        except Exception:
            logger.warning(
                "Failed to create sandbox for task %s, falling back to in-process",
                task.id, exc_info=True,
            )

    # Load project memory for this task's project
    project_memory_store = None
    if settings.MEMORY_ENABLED and task.project_id:
        try:
            from app.worker.memory import ProjectMemoryStore
            project_memory_store = ProjectMemoryStore(str(task.project_id))
        except Exception:
            logger.warning("Failed to init memory store for project %s", task.project_id, exc_info=True)

    # Group stages by order for parallel execution
    stage_groups = _group_stages_by_order(sorted_stages, task)
    stage_index_base = 0

    for group in stage_groups:
        # Skip group if all stages are completed (resume from failure)
        all_completed = all(s.status == "completed" for s in group)
        if all_completed:
            for stage in group:
                logger.info(
                    "Skipping completed stage %s for task %s (resuming)",
                    stage.stage_name, task.id,
                )
                prior_outputs.append({
                    "stage": stage.stage_name,
                    "output": stage.output_summary or "",
                })
                compressed = await _compress_with_log(task, stage, stage.output_summary or "")
                if compressed is not None:
                    compression.add(compressed)
                else:
                    logger.warning("Compression failed for resumed stage %s", stage.stage_name)
            stage_index_base += len(group)
            continue

        # Check cancellation before each group
        if await _is_cancelled(session, task.id):
            logger.info("Task %s cancelled, stopping execution", task.id)
            await event_collector.record_audit(
                session,
                agent_role="orchestrator",
                action_type="task_cancelled",
                detail={"task_id": task.id, "at_stage": group[0].stage_name},
            )
            return

        # Execute stages in this group (parallel if multiple)
        if len(group) == 1:
            stage = group[0]
            result = await _execute_single_stage(
                session, task, stage, stage_index_base,
                prior_outputs, compression, project_memory_store,
                repo_context, stage_defs, worktree_path, sandbox_info,
            )
            if result is None:
                return  # stage failed or circuit breaker
            prior_outputs.append({"stage": stage.stage_name, "output": result})
            compressed = await _compress_with_log(task, stage, result)
            if compressed is not None:
                compression.add(compressed)
            else:
                logger.warning("Compression failed for stage %s", stage.stage_name)

            await _record_stage_audit(session, stage)
            if await _check_circuit_breaker(session, task, stage):
                return

            gate_type = gates.get(stage.stage_name)
            if gate_type:
                if not await _handle_gate(session, task, stage, gate_type, result):
                    await _fail_task(session, task, f"Gate rejected after stage {stage.stage_name}")
                    return
        else:
            # Parallel execution for same-order stages
            logger.info(
                "Executing %d stages in parallel: %s",
                len(group), [s.stage_name for s in group],
            )
            tasks_map = {}
            for stage in group:
                if stage.status == "completed":
                    prior_outputs.append({
                        "stage": stage.stage_name,
                        "output": stage.output_summary or "",
                    })
                    continue
                coro = _execute_single_stage(
                    session, task, stage, stage_index_base,
                    prior_outputs, compression, project_memory_store,
                    repo_context, stage_defs, worktree_path, sandbox_info,
                )
                tasks_map[stage.stage_name] = (stage, asyncio.create_task(coro))

            # Await all parallel stages
            for stage_name, (stage, atask) in tasks_map.items():
                try:
                    result = await atask
                except Exception as e:
                    logger.exception("Parallel stage %s failed", stage_name)
                    await mark_stage_failed(session, task, stage, str(e))
                    # Cancel remaining parallel tasks
                    for _, (_, other_task) in tasks_map.items():
                        if not other_task.done():
                            other_task.cancel()
                    from app.worker.agents import close_agents_for_task
                    close_agents_for_task(str(task.id))
                    await _fail_task(session, task, f"Stage {stage_name} failed: {e}")
                    return

                if result is None:
                    return
                prior_outputs.append({"stage": stage_name, "output": result})
                compressed = await _compress_with_log(task, stage, result)
                if compressed is not None:
                    compression.add(compressed)
                else:
                    logger.warning("Compression failed for stage %s", stage_name)
                await _record_stage_audit(session, stage)

            if await _check_circuit_breaker(session, task, group[0]):
                return

            # Gates for parallel stages (check each)
            for stage in group:
                gate_type = gates.get(stage.stage_name)
                if gate_type:
                    output = stage.output_summary or ""
                    if not await _handle_gate(session, task, stage, gate_type, output):
                        await _fail_task(
                            session, task, f"Gate rejected after stage {stage.stage_name}",
                        )
                        return

        stage_index_base += len(group)

    # All stages completed — extract memories from this task
    if settings.MEMORY_ENABLED and project_memory_store and prior_outputs:
        memory_corr = f"memory-extract-{uuid.uuid4().hex}"
        memory_started_at = time.monotonic()
        memory_started_log_id = await _emit_system_log(
            task,
            event_type="memory_extract_started",
            status="running",
            correlation_id=memory_corr,
            response_body={"stage_output_count": len(prior_outputs)},
        )
        try:
            from app.worker.memory_extractor import extract_and_store_memories
            await extract_and_store_memories(
                project_id=str(task.project_id),
                task_id=str(task.id),
                task_title=task.title,
                stage_outputs=prior_outputs,
            )
            duration_ms = round((time.monotonic() - memory_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="memory_extract_finished",
                status="success",
                correlation_id=memory_corr,
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=memory_started_log_id,
                started_at_monotonic=memory_started_at,
                status="success",
            )
        except Exception:
            logger.warning("Memory extraction failed for task %s", task.id, exc_info=True)
            duration_ms = round((time.monotonic() - memory_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="memory_extract_finished",
                status="failed",
                correlation_id=memory_corr,
                duration_ms=duration_ms,
                response_body={"error": "memory_extract_failed"},
            )
            await _close_started_system_log(
                started_log_id=memory_started_log_id,
                started_at_monotonic=memory_started_at,
                status="failed",
                result="memory_extract_failed",
            )

    # Worktree: commit, push, and optionally create PR
    if worktree_mgr and worktree_path:
        worktree_commit_corr = f"worktree-commit-{uuid.uuid4().hex}"
        worktree_commit_started_at = time.monotonic()
        worktree_commit_started_log_id = await _emit_system_log(
            task,
            event_type="worktree_commit_push_started",
            status="running",
            correlation_id=worktree_commit_corr,
            response_body={"worktree_path": worktree_path},
        )
        try:
            branch = await worktree_mgr.commit_and_push(
                task_id=str(task.id),
                commit_message=f"feat: {task.title}\n\nTask-ID: {task.id}",
            )
            duration_ms = round((time.monotonic() - worktree_commit_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_commit_push_finished",
                status="success",
                correlation_id=worktree_commit_corr,
                response_body={"branch": branch},
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=worktree_commit_started_log_id,
                started_at_monotonic=worktree_commit_started_at,
                status="success",
            )
            if branch:
                task.branch_name = branch
                await session.commit()
            if branch and settings.WORKTREE_AUTO_PR:
                pr_corr = f"worktree-pr-{uuid.uuid4().hex}"
                pr_started_at = time.monotonic()
                pr_started_log_id = await _emit_system_log(
                    task,
                    event_type="worktree_pr_started",
                    status="running",
                    correlation_id=pr_corr,
                    response_body={"branch": branch},
                )
                pr_body = f"Automated PR for task: {task.title}\n\nTask ID: {task.id}"
                pr_url = await worktree_mgr.create_pr(
                    task_id=str(task.id),
                    title=task.title,
                    body=pr_body,
                    base_branch=task.project.branch or "main",
                )
                if pr_url:
                    task.pr_url = pr_url
                    await session.commit()
                    logger.info("PR created for task %s: %s", task.id, pr_url)
                pr_duration_ms = round((time.monotonic() - pr_started_at) * 1000, 2)
                await _emit_system_log(
                    task,
                    event_type="worktree_pr_finished",
                    status="success",
                    correlation_id=pr_corr,
                    response_body={"pr_url": pr_url},
                    duration_ms=pr_duration_ms,
                )
                await _close_started_system_log(
                    started_log_id=pr_started_log_id,
                    started_at_monotonic=pr_started_at,
                    status="success",
                )
        except Exception:
            logger.warning("Worktree commit/push failed for task %s", task.id, exc_info=True)
            duration_ms = round((time.monotonic() - worktree_commit_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_commit_push_finished",
                status="failed",
                correlation_id=worktree_commit_corr,
                response_body={"error": "worktree_commit_push_failed"},
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=worktree_commit_started_log_id,
                started_at_monotonic=worktree_commit_started_at,
                status="failed",
                result="worktree_commit_push_failed",
            )

        # Cleanup worktree
        cleanup_corr = f"worktree-cleanup-{uuid.uuid4().hex}"
        cleanup_started_at = time.monotonic()
        cleanup_started_log_id = await _emit_system_log(
            task,
            event_type="worktree_cleanup_started",
            status="running",
            correlation_id=cleanup_corr,
        )
        try:
            await worktree_mgr.cleanup_worktree(str(task.id))
            cleanup_duration_ms = round((time.monotonic() - cleanup_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_cleanup_finished",
                status="success",
                correlation_id=cleanup_corr,
                duration_ms=cleanup_duration_ms,
            )
            await _close_started_system_log(
                started_log_id=cleanup_started_log_id,
                started_at_monotonic=cleanup_started_at,
                status="success",
            )
        except Exception:
            logger.warning("Worktree cleanup failed for task %s", task.id, exc_info=True)
            cleanup_duration_ms = round((time.monotonic() - cleanup_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_cleanup_finished",
                status="failed",
                correlation_id=cleanup_corr,
                response_body={"error": "worktree_cleanup_failed"},
                duration_ms=cleanup_duration_ms,
            )
            await _close_started_system_log(
                started_log_id=cleanup_started_log_id,
                started_at_monotonic=cleanup_started_at,
                status="failed",
                result="worktree_cleanup_failed",
            )

    # Clean up sandbox container
    if sandbox_mgr and sandbox_info:
        try:
            await sandbox_mgr.destroy(str(task.id))
        except Exception:
            logger.warning("Sandbox cleanup failed for task %s", task.id, exc_info=True)

    # Clean up per-task agents
    from app.worker.agents import close_agents_for_task
    close_agents_for_task(str(task.id))
    await _complete_task(session, task)


async def _execute_single_stage(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    stage_index: int,
    prior_outputs: List[Dict[str, str]],
    compression: CompressionResult,
    project_memory_store,
    repo_context: Optional[str],
    stage_defs: Dict[str, dict],
    worktree_path: Optional[str] = None,
    sandbox_info=None,
) -> Optional[str]:
    """Execute a single stage with model routing and retry context.

    Returns output text on success, None on failure (task is already marked failed).
    """
    # Resolve per-stage model override from template
    sdef = stage_defs.get(stage.stage_name, {})
    stage_model = sdef.get("model")  # None if not specified

    # Build project memory for the current role
    project_memory: Optional[str] = None
    if project_memory_store:
        try:
            project_memory = project_memory_store.get_memory_for_role(stage.agent_role)
        except Exception:
            logger.warning("Failed to load memory for role %s", stage.agent_role, exc_info=True)

    # Build compressed prior context via sliding window
    compressed_prior = compression.build_prior_context(stage_index)

    # Build retry context if this stage previously failed (smart retry)
    retry_context: Optional[Dict[str, str]] = None
    if stage.error_message or stage.output_summary:
        # Stage has prior failure info — inject it for smarter retry
        if stage.error_message:
            retry_context = {
                "error": stage.error_message,
                "prior_output": (stage.output_summary or "")[:2000],
            }

    # Determine working directory: worktree for code-producing roles, tmpdir otherwise
    _CODE_ROLES = {"coding", "test"}
    effective_workdir = worktree_path if (worktree_path and stage.agent_role in _CODE_ROLES) else None

    # Route to sandbox container or in-process execution
    use_sandbox = sandbox_info is not None and stage.agent_role in _CODE_ROLES

    try:
        if use_sandbox:
            output = await execute_stage_sandboxed(
                session, task, stage, prior_outputs,
                sandbox_info=sandbox_info,
                compressed_outputs=compressed_prior if compressed_prior else None,
                project_memory=project_memory,
                repo_context=repo_context,
                retry_context=retry_context,
                stage_model=stage_model,
            )
        else:
            output = await execute_stage(
                session, task, stage, prior_outputs,
                compressed_outputs=compressed_prior if compressed_prior else None,
                project_memory=project_memory,
                repo_context=repo_context,
                retry_context=retry_context,
                stage_model=stage_model,
                workdir_override=effective_workdir,
            )
        return output
    except Exception as e:
        error_msg = str(e)
        logger.exception("Stage %s failed for task %s", stage.stage_name, task.id)
        await mark_stage_failed(session, task, stage, error_msg)
        from app.worker.agents import close_agents_for_task
        close_agents_for_task(str(task.id))
        await _fail_task(session, task, f"Stage {stage.stage_name} failed: {error_msg}")
        return None


async def _compress_with_log(
    task: TaskModel,
    stage: TaskStageModel,
    output: str,
) -> Any | None:
    correlation_id = f"compression-{uuid.uuid4().hex}"
    compression_started_at = time.monotonic()
    compression_started_log_id = await _emit_system_log(
        task,
        stage=stage,
        event_type="compression_started",
        status="running",
        correlation_id=correlation_id,
        response_body={"output_length": len(output or "")},
    )
    try:
        compressed = await compress_stage_output(stage.stage_name, output)
    except Exception as exc:
        duration_ms = round((time.monotonic() - compression_started_at) * 1000, 2)
        await _emit_system_log(
            task,
            stage=stage,
            event_type="compression_finished",
            status="failed",
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            response_body={"error": str(exc)},
        )
        await _close_started_system_log(
            started_log_id=compression_started_log_id,
            started_at_monotonic=compression_started_at,
            status="failed",
            result=str(exc),
        )
        return None

    duration_ms = round((time.monotonic() - compression_started_at) * 1000, 2)
    await _emit_system_log(
        task,
        stage=stage,
        event_type="compression_finished",
        status="success",
        correlation_id=correlation_id,
        duration_ms=duration_ms,
        response_body={
            "l0_length": len(compressed.l0 or ""),
            "l1_length": len(compressed.l1 or ""),
            "l2_length": len(compressed.l2 or ""),
        },
    )
    await _close_started_system_log(
        started_log_id=compression_started_log_id,
        started_at_monotonic=compression_started_at,
        status="success",
    )
    return compressed


async def _record_stage_audit(session: AsyncSession, stage: TaskStageModel) -> None:
    """Record audit event for a completed stage."""
    await event_collector.record_audit(
        session,
        agent_role=stage.agent_role,
        action_type=f"stage_{stage.stage_name}_completed",
        detail={
            "task_id": stage.task_id,
            "stage_id": stage.id,
            "tokens_used": stage.tokens_used,
            "duration_seconds": stage.duration_seconds,
        },
    )


async def _check_circuit_breaker(
    session: AsyncSession, task: TaskModel, stage: TaskStageModel,
) -> bool:
    """Check circuit breaker limits. Returns True if breaker tripped (task failed)."""
    if task.total_tokens <= settings.CB_MAX_TOKENS_PER_TASK and \
       task.total_cost_rmb <= settings.CB_MAX_COST_PER_TASK_RMB:
        return False

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
    return True


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
    gate_corr = f"gate-wait-{uuid.uuid4().hex}"
    gate_started_at = time.monotonic()
    gate_started_log_id = await _emit_system_log(
        task,
        stage=stage,
        event_type="gate_wait_started",
        status="running",
        correlation_id=gate_corr,
        response_body={"gate_type": gate_type},
    )

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

    # External notification for gate approval
    await notify_gate_created(gate.id, task.id, stage.stage_name, gate_type)

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
            duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                stage=stage,
                event_type="gate_wait_timeout",
                status="cancelled",
                correlation_id=gate_corr,
                duration_ms=duration_ms,
                response_body={"gate_id": gate.id, "elapsed_seconds": elapsed},
            )
            await _close_started_system_log(
                started_log_id=gate_started_log_id,
                started_at_monotonic=gate_started_at,
                status="cancelled",
                result="gate_wait_timeout",
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
            duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                stage=stage,
                event_type="gate_wait_approved",
                status="success",
                correlation_id=gate_corr,
                duration_ms=duration_ms,
                response_body={"gate_id": gate.id},
            )
            await _close_started_system_log(
                started_log_id=gate_started_log_id,
                started_at_monotonic=gate_started_at,
                status="success",
            )
            return True
        elif gate.status == "rejected":
            logger.info("Gate %s rejected", gate.id)
            duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                stage=stage,
                event_type="gate_wait_rejected",
                status="failed",
                correlation_id=gate_corr,
                duration_ms=duration_ms,
                response_body={"gate_id": gate.id},
            )
            await _close_started_system_log(
                started_log_id=gate_started_log_id,
                started_at_monotonic=gate_started_at,
                status="failed",
                result="gate_rejected",
            )
            return False

        # Also check for task cancellation during gate wait
        if await _is_cancelled(session, task.id):
            logger.info("Task %s cancelled while waiting for gate", task.id)
            duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                stage=stage,
                event_type="gate_wait_cancelled",
                status="cancelled",
                correlation_id=gate_corr,
                duration_ms=duration_ms,
                response_body={"gate_id": gate.id},
            )
            await _close_started_system_log(
                started_log_id=gate_started_log_id,
                started_at_monotonic=gate_started_at,
                status="cancelled",
                result="task_cancelled",
            )
            return False

    # Worker shutting down
    duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
    await _emit_system_log(
        task,
        stage=stage,
        event_type="gate_wait_cancelled",
        status="cancelled",
        correlation_id=gate_corr,
        duration_ms=duration_ms,
        response_body={"gate_id": gate.id, "reason": "worker_stopped"},
    )
    await _close_started_system_log(
        started_log_id=gate_started_log_id,
        started_at_monotonic=gate_started_at,
        status="cancelled",
        result="worker_stopped",
    )
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


def _parse_stage_defs(task: TaskModel) -> Dict[str, dict]:
    """Parse template stage definitions into a dict keyed by stage name.

    Returns {stage_name: {"name": ..., "agent_role": ..., "order": ..., "model": ..., ...}}.
    """
    if not task.template:
        return {}
    try:
        stage_defs = json.loads(task.template.stages) if task.template.stages else []
    except (json.JSONDecodeError, TypeError):
        return {}
    return {sd["name"]: sd for sd in stage_defs}


def _group_stages_by_order(
    stages: List[TaskStageModel], task: TaskModel,
) -> List[List[TaskStageModel]]:
    """Group stages by their template order for parallel execution.

    Stages with the same order value form a parallel group.
    Returns a list of groups, each group is a list of stages to execute concurrently.
    """
    if not task.template:
        return [[s] for s in stages]

    try:
        stage_defs = json.loads(task.template.stages) if task.template.stages else []
    except (json.JSONDecodeError, TypeError):
        return [[s] for s in stages]

    order_map = {sd["name"]: sd.get("order", i) for i, sd in enumerate(stage_defs)}

    groups: Dict[int, List[TaskStageModel]] = defaultdict(list)
    for stage in stages:
        order = order_map.get(stage.stage_name, 999)
        groups[order].append(stage)

    return [groups[k] for k in sorted(groups.keys())]


async def _complete_task(session: AsyncSession, task: TaskModel) -> None:
    """Mark task as completed, broadcast, and send external notification."""
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

    # External notification
    await notify_task_completed(task.id, task.title, task.total_tokens)

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
    """Mark task as failed, broadcast, and send external notification."""
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

    # External notification
    await notify_task_failed(task.id, task.title, reason)

    logger.error("Task %s failed: %s", task.id, reason)
