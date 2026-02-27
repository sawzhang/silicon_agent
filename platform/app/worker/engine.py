"""Worker engine: poll DB for pending tasks, orchestrate stage execution, handle gates."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
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
from app.websocket.events import CB_TRIGGERED, GATE_CREATED, TASK_STAGE_UPDATE, TASK_STATUS_CHANGED
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


def _resolve_sandbox_fallback_mode() -> str:
    raw = (settings.SANDBOX_FALLBACK_MODE or "graceful").strip().lower()
    return raw if raw in {"graceful", "strict"} else "graceful"


def _resolve_sandbox_workspace(task_id: str, worktree_path: Optional[str]) -> tuple[str, str]:
    if worktree_path:
        return worktree_path, "worktree"
    return str(Path(settings.SANDBOX_WORKSPACE_BASE_DIR) / task_id), "fallback"


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


async def _setup_worktree(task: TaskModel) -> tuple[Optional[str], Any]:
    """Create git worktree for the task if enabled. Returns (worktree_path, worktree_mgr)."""
    if not (settings.WORKTREE_ENABLED and task.project and task.project.repo_local_path):
        return None, None

    worktree_path: Optional[str] = None
    worktree_mgr = get_worktree_manager(task.project.repo_local_path)
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
        worktree_path = await worktree_mgr.create_worktree(
            task_id=str(task.id),
            task_title=task.title,
            base_branch=task.project.branch or "main",
            target_branch=task.target_branch,
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

    return worktree_path, worktree_mgr


async def _setup_sandbox(
    task: TaskModel, worktree_path: Optional[str],
) -> tuple[Any, Any, Optional[str]]:
    """Create sandbox container if enabled.

    Returns (sandbox_info, sandbox_mgr, sandbox_required_error).
    """
    if not settings.SANDBOX_ENABLED:
        return None, None, None

    from app.worker.sandbox import SandboxCreateResult, get_sandbox_manager

    sandbox_mgr = get_sandbox_manager()
    sandbox_image = None
    if task.project and task.project.sandbox_image:
        sandbox_image = task.project.sandbox_image
    fallback_mode = _resolve_sandbox_fallback_mode()
    resolved_workspace, workspace_source = _resolve_sandbox_workspace(str(task.id), worktree_path)

    workspace_prepare_error_code: Optional[str] = None
    workspace_prepare_error: Optional[str] = None
    workspace_path = Path(resolved_workspace)
    if workspace_source == "fallback":
        try:
            workspace_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            workspace_prepare_error_code = "workspace_prepare_failed"
            workspace_prepare_error = str(exc)
    elif not workspace_path.exists() or not workspace_path.is_dir():
        workspace_prepare_error_code = "worktree_workspace_not_found"
        workspace_prepare_error = (
            f"Worktree path does not exist or is not directory: {resolved_workspace}"
        )

    sandbox_corr = f"sandbox-create-{uuid.uuid4().hex}"
    sandbox_started_at = time.monotonic()
    sandbox_started_log_id = await _emit_system_log(
        task,
        event_type="sandbox_create_started",
        status="running",
        correlation_id=sandbox_corr,
        response_body={
            "workspace": resolved_workspace,
            "workspace_source": workspace_source,
            "fallback_mode": fallback_mode,
            "image": sandbox_image or settings.SANDBOX_IMAGE,
        },
    )

    sandbox_info = None
    sandbox_required_error: Optional[str] = None
    try:
        if workspace_prepare_error_code:
            create_result = SandboxCreateResult(
                info=None,
                workspace=resolved_workspace,
                workspace_source=workspace_source,
                error_code=workspace_prepare_error_code,
                error_message=workspace_prepare_error,
            )
        else:
            create_result = await sandbox_mgr.create(
                str(task.id),
                workspace=resolved_workspace,
                workspace_source=workspace_source,
                image=sandbox_image,
            )

        sandbox_info = create_result.info
        duration_ms = round((time.monotonic() - sandbox_started_at) * 1000, 2)
        if sandbox_info:
            logger.info("Task %s using sandbox container: %s", task.id, sandbox_info.container_name)
            await _emit_system_log(
                task,
                event_type="sandbox_create_finished",
                status="success",
                correlation_id=sandbox_corr,
                duration_ms=duration_ms,
                response_body={
                    "workspace": create_result.workspace,
                    "workspace_source": create_result.workspace_source,
                    "container_name": sandbox_info.container_name,
                },
            )
            await _close_started_system_log(
                started_log_id=sandbox_started_log_id,
                started_at_monotonic=sandbox_started_at,
                status="success",
            )
        else:
            error_code = create_result.error_code or "sandbox_create_failed"
            error_message = create_result.error_message or "sandbox_create_failed"
            sandbox_required_error = f"{error_code}: {error_message}"
            await _emit_system_log(
                task,
                event_type="sandbox_create_finished",
                status="failed",
                correlation_id=sandbox_corr,
                duration_ms=duration_ms,
                result=sandbox_required_error,
                response_body={
                    "workspace": create_result.workspace,
                    "workspace_source": create_result.workspace_source,
                    "error_code": error_code,
                    "error": error_message,
                },
            )
            await _close_started_system_log(
                started_log_id=sandbox_started_log_id,
                started_at_monotonic=sandbox_started_at,
                status="failed",
                result=sandbox_required_error,
            )
            if fallback_mode == "graceful":
                await _emit_system_log(
                    task,
                    event_type="sandbox_fallback",
                    status="success",
                    correlation_id=sandbox_corr,
                    response_body={
                        "fallback_mode": fallback_mode,
                        "execution_mode": "in_process",
                        "workspace": create_result.workspace,
                        "error_code": error_code,
                        "error": error_message,
                    },
                )
                logger.warning(
                    "Sandbox creation failed for task %s (%s), falling back to in-process",
                    task.id,
                    sandbox_required_error,
                )
            else:
                logger.error(
                    "Sandbox creation failed for task %s in strict mode: %s",
                    task.id,
                    sandbox_required_error,
                )
    except Exception:
        if fallback_mode == "graceful":
            logger.warning(
                "Failed to create sandbox for task %s, falling back to in-process",
                task.id,
                exc_info=True,
            )
        else:
            logger.error(
                "Failed to create sandbox for task %s in strict mode",
                task.id,
                exc_info=True,
            )
        duration_ms = round((time.monotonic() - sandbox_started_at) * 1000, 2)
        sandbox_required_error = "sandbox_create_exception"
        await _emit_system_log(
            task,
            event_type="sandbox_create_finished",
            status="failed",
            correlation_id=sandbox_corr,
            duration_ms=duration_ms,
            result=sandbox_required_error,
            response_body={"error_code": "sandbox_create_exception"},
        )
        await _close_started_system_log(
            started_log_id=sandbox_started_log_id,
            started_at_monotonic=sandbox_started_at,
            status="failed",
            result=sandbox_required_error,
        )
        if fallback_mode == "graceful":
            await _emit_system_log(
                task,
                event_type="sandbox_fallback",
                status="success",
                correlation_id=sandbox_corr,
                response_body={
                    "fallback_mode": "graceful",
                    "execution_mode": "in_process",
                    "error_code": "sandbox_create_exception",
                },
            )

    return sandbox_info, sandbox_mgr, sandbox_required_error


async def _finalize_task_resources(
    session: AsyncSession,
    task: TaskModel,
    prior_outputs: List[Dict[str, str]],
    project_memory_store: Any,
    worktree_mgr: Any,
    worktree_path: Optional[str],
    sandbox_mgr: Any,
    sandbox_info: Any,
) -> None:
    """Post-completion: extract memories, commit/push worktree, and cleanup resources."""
    # Extract memories from this task
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

    # Aggregate skill invocation metrics from task logs
    if settings.SKILL_FEEDBACK_ENABLED:
        try:
            from app.services.skill_feedback_service import aggregate_skill_metrics
            await aggregate_skill_metrics(session, str(task.id))
        except Exception:
            logger.warning(
                "Skill metrics aggregation failed for task %s", task.id, exc_info=True
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
                target_branch=task.target_branch,
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

    await _cleanup_runtime_resources(task, worktree_mgr, worktree_path, sandbox_mgr, sandbox_info)


async def _cleanup_runtime_resources(
    task: TaskModel,
    worktree_mgr: Any,
    worktree_path: Optional[str],
    sandbox_mgr: Any,
    sandbox_info: Any,
) -> None:
    """Always cleanup runtime resources regardless of success/failure paths."""
    # Cleanup worktree
    if worktree_mgr and worktree_path:
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
    # Phase 2.1: Track structured outputs for condition evaluation
    structured_outputs: Dict[str, dict] = {}
    worktree_path: Optional[str] = None
    worktree_mgr: Any = None
    sandbox_info: Any = None
    sandbox_mgr: Any = None
    resources_finalized = False

    try:
        # Load repo context from project
        repo_context: Optional[str] = None
        if task.project and task.project.repo_tree:
            repo_context = _build_repo_context(task.project)

        # Setup git worktree and sandbox
        worktree_path, worktree_mgr = await _setup_worktree(task)
        sandbox_info, sandbox_mgr, sandbox_required_error = await _setup_sandbox(task, worktree_path)

        # Load project memory for this task's project
        project_memory_store = None
        if settings.MEMORY_ENABLED and task.project_id:
            try:
                from app.worker.memory import ProjectMemoryStore
                project_memory_store = ProjectMemoryStore(str(task.project_id))
            except Exception:
                logger.warning("Failed to init memory store for project %s", task.project_id, exc_info=True)

        # Phase 3.1: Graph-based execution when enabled
        if settings.GRAPH_EXECUTION_ENABLED and task.template:
            await _process_task_graph(
                session, task, sorted_stages, stage_defs, gates,
                prior_outputs, compression, structured_outputs,
                project_memory_store, repo_context, worktree_path, sandbox_info, sandbox_required_error,
            )
            # Finalize and return
            await _finalize_task_resources(
                session, task, prior_outputs, project_memory_store,
                worktree_mgr, worktree_path, sandbox_mgr, sandbox_info,
            )
            resources_finalized = True
            from app.worker.agents import close_agents_for_task
            close_agents_for_task(str(task.id))
            await _complete_task(session, task)
            return

        # Group stages by order for parallel execution (legacy linear mode)
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
                # Check gates for resumed stages
                for stage in group:
                    gate_def = gates.get(stage.stage_name)
                    if gate_def:
                        gate_result = await _handle_gate_with_retry(
                            session, task, stage, gate_def,
                            stage.output_summary or "", stage_index_base, prior_outputs, compression,
                            project_memory_store, repo_context, stage_defs,
                            worktree_path, sandbox_info, sandbox_required_error,
                        )
                        if gate_result is None:
                            return
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

                # Phase 2.1: Check condition before executing
                if _should_skip_stage(stage, stage_defs, structured_outputs):
                    stage.status = "skipped"
                    await session.commit()
                    await _safe_broadcast(TASK_STAGE_UPDATE, {
                        "task_id": task.id, "stage_id": stage.id,
                        "stage_name": stage.stage_name, "status": "skipped",
                    })
                    stage_index_base += len(group)
                    continue

                result = await _execute_single_stage(
                    session, task, stage, stage_index_base,
                    prior_outputs, compression, project_memory_store,
                    repo_context, stage_defs, worktree_path, sandbox_info,
                    sandbox_required_error=sandbox_required_error,
                )
                if result is None:
                    return  # stage failed or circuit breaker
                prior_outputs.append({"stage": stage.stage_name, "output": result})
                compressed = await _compress_with_log(task, stage, result)
                if compressed is not None:
                    compression.add(compressed)
                else:
                    logger.warning("Compression failed for stage %s", stage.stage_name)

                # Phase 1.1/2.1: Collect structured output for conditions
                if stage.output_structured:
                    structured_outputs[stage.stage_name] = stage.output_structured

                await _record_stage_audit(session, stage)
                if await _check_circuit_breaker(session, task, stage):
                    return

                # Phase 2.3: Dynamic gate insertion for low confidence
                if await _maybe_insert_dynamic_gate(session, task, stage, result):
                    pass  # Gate handled, continue

                gate_def = gates.get(stage.stage_name)
                if gate_def:
                    gate_result = await _handle_gate_with_retry(
                        session, task, stage, gate_def,
                        result, stage_index_base, prior_outputs, compression,
                        project_memory_store, repo_context, stage_defs,
                        worktree_path, sandbox_info, sandbox_required_error,
                    )
                    if gate_result is None:
                        return  # task failed or gate rejected without retries
                    # Update result to the latest output after possible retries
                    result = gate_result

                # Phase 3.2: Interactive planning — pause after parse stage
                if await _check_interactive_planning(session, task, stage, result):
                    return  # Task paused for plan review

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
                        if stage.output_structured:
                            structured_outputs[stage.stage_name] = stage.output_structured
                        continue
                    # Phase 2.1: Check condition for parallel stages too
                    if _should_skip_stage(stage, stage_defs, structured_outputs):
                        stage.status = "skipped"
                        await session.commit()
                        await _safe_broadcast(TASK_STAGE_UPDATE, {
                            "task_id": task.id, "stage_id": stage.id,
                            "stage_name": stage.stage_name, "status": "skipped",
                        })
                        continue
                    coro = _execute_single_stage(
                        session, task, stage, stage_index_base,
                        prior_outputs, compression, project_memory_store,
                        repo_context, stage_defs, worktree_path, sandbox_info,
                        sandbox_required_error=sandbox_required_error,
                    )
                    tasks_map[stage.stage_name] = (stage, asyncio.create_task(coro))

                # Await all parallel stages
                for stage_name, (stage, atask) in tasks_map.items():
                    try:
                        result = await atask
                    except Exception as e:
                        logger.exception("Parallel stage %s failed", stage_name)
                        await mark_stage_failed(session, task, stage, str(e), error=e)
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
                    # Phase 1.1/2.1: Collect structured output
                    if stage.output_structured:
                        structured_outputs[stage.stage_name] = stage.output_structured
                    await _record_stage_audit(session, stage)

                if await _check_circuit_breaker(session, task, group[0]):
                    return

                # Gates for parallel stages (check each)
                for stage in group:
                    gate_def = gates.get(stage.stage_name)
                    if gate_def:
                        output = stage.output_summary or ""
                        gate_result = await _handle_gate_with_retry(
                            session, task, stage, gate_def,
                            output, stage_index_base, prior_outputs, compression,
                            project_memory_store, repo_context, stage_defs,
                            worktree_path, sandbox_info, sandbox_required_error,
                        )
                        if gate_result is None:
                            return

            stage_index_base += len(group)

        # Finalize: extract memories, commit worktree, cleanup resources
        await _finalize_task_resources(
            session, task, prior_outputs, project_memory_store,
            worktree_mgr, worktree_path, sandbox_mgr, sandbox_info,
        )
        resources_finalized = True

        # Clean up per-task agents and mark completed
        from app.worker.agents import close_agents_for_task
        close_agents_for_task(str(task.id))
        await _complete_task(session, task)
    finally:
        if not resources_finalized:
            await _cleanup_runtime_resources(
                task,
                worktree_mgr,
                worktree_path,
                sandbox_mgr,
                sandbox_info,
            )


async def _process_task_graph(
    session: AsyncSession,
    task: TaskModel,
    sorted_stages: List[TaskStageModel],
    stage_defs: Dict[str, dict],
    gates: Dict[str, dict],
    prior_outputs: List[Dict[str, str]],
    compression: CompressionResult,
    structured_outputs: Dict[str, dict],
    project_memory_store,
    repo_context: Optional[str],
    worktree_path: Optional[str] = None,
    sandbox_info=None,
    sandbox_required_error: Optional[str] = None,
) -> None:
    """Phase 3.1: Graph-driven stage execution loop.

    Uses StageGraph to determine ready stages based on dependencies,
    executing them in parallel when multiple are ready simultaneously.
    Supports failure redirects and cycle-limited loops.
    """
    from app.worker.graph import StageGraph

    # Build graph from template
    template_stages = task.template.stages if task.template else None
    graph = StageGraph.from_template_stages(template_stages)

    # Validate graph
    errors = graph.validate()
    if errors:
        logger.error("Invalid stage graph for task %s: %s", task.id, errors)
        await _fail_task(session, task, f"Invalid stage graph: {'; '.join(errors)}")
        return

    # Build stage lookup
    stage_map = {s.stage_name: s for s in sorted_stages}

    # Track execution state
    completed: set[str] = set()
    running: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    execution_counts: Dict[str, int] = defaultdict(int)

    for stage in sorted_stages:
        if stage.status == "completed":
            completed.add(stage.stage_name)
            prior_outputs.append({
                "stage": stage.stage_name,
                "output": stage.output_summary or "",
            })
            if stage.output_structured:
                structured_outputs[stage.stage_name] = stage.output_structured
                
            # Phase 2: Missing check! Resume outstanding gates for completed stages in graph!
            gate_def = gates.get(stage.stage_name)
            if gate_def:
                gate_result = await _handle_gate_with_retry(
                    session, task, stage, gate_def,
                    stage.output_summary or "", len(completed) + len(skipped),  # stage_index approximate
                    prior_outputs, compression,
                    project_memory_store, repo_context, stage_defs,
                    worktree_path, sandbox_info,
                )
                if gate_result is None:
                    return
        elif stage.status == "skipped":
            skipped.add(stage.stage_name)
        execution_counts[stage.stage_name] = stage.execution_count

    max_iterations = settings.GRAPH_MAX_LOOP_ITERATIONS * len(graph.nodes)
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Check cancellation
        if await _is_cancelled(session, task.id):
            logger.info("Task %s cancelled during graph execution", task.id)
            return

        # Get ready stages
        ready = graph.get_ready_stages(completed, running, failed, skipped, execution_counts)
        if not ready:
            if running:
                await asyncio.sleep(1)
                continue
            # No ready, no running — we're done or stuck
            if failed:
                unresolved = [n for n in failed if n not in completed]
                if unresolved:
                    await _fail_task(
                        session, task,
                        f"Graph execution stuck: stages {unresolved} failed with no redirect",
                    )
                    return
            break  # All done

        # Execute ready stages (parallel if multiple)
        stage_index = len(completed) + len(skipped)
        if len(ready) == 1:
            node = ready[0]
            stage = stage_map.get(node.name)
            if not stage:
                logger.warning("Stage %s in graph but not in task stages", node.name)
                skipped.add(node.name)
                continue

            # Check condition
            if _should_skip_stage(stage, stage_defs, structured_outputs):
                stage.status = "skipped"
                await session.commit()
                skipped.add(node.name)
                continue

            execution_counts[node.name] += 1
            stage.execution_count = execution_counts[node.name]
            await session.commit()

            result = await _execute_single_stage(
                session, task, stage, stage_index,
                prior_outputs, compression, project_memory_store,
                repo_context, stage_defs, worktree_path, sandbox_info,
                sandbox_required_error=sandbox_required_error,
            )
            if result is None:
                failed.add(node.name)
                # Check for failure redirect
                redirect = graph.get_failure_redirect(node.name)
                if redirect and redirect in stage_map:
                    logger.info(
                        "Stage %s failed, redirecting to %s", node.name, redirect,
                    )
                    # Reset the redirect target for re-execution
                    redirect_stage = stage_map[redirect]
                    redirect_stage.status = "pending"
                    redirect_stage.error_message = None
                    redirect_stage.output_summary = None
                    await session.commit()
                    completed.discard(redirect)
                    failed.discard(redirect)
                else:
                    await _fail_task(session, task, f"Stage {node.name} failed in graph")
                    return
                continue

            completed.add(node.name)
            prior_outputs.append({"stage": node.name, "output": result})
            compressed = await _compress_with_log(task, stage, result)
            if compressed is not None:
                compression.add(compressed)
            if stage.output_structured:
                structured_outputs[node.name] = stage.output_structured

            # Handle gates
            gate_def = gates.get(node.name)
            if gate_def:
                gate_result = await _handle_gate_with_retry(
                    session, task, stage, gate_def,
                    result, stage_index, prior_outputs, compression,
                    project_memory_store, repo_context, stage_defs,
                    worktree_path, sandbox_info, sandbox_required_error,
                )
                if gate_result is None:
                    return
        else:
            # Parallel execution
            tasks_async = {}
            for node in ready:
                stage = stage_map.get(node.name)
                if not stage:
                    skipped.add(node.name)
                    continue
                if _should_skip_stage(stage, stage_defs, structured_outputs):
                    stage.status = "skipped"
                    await session.commit()
                    skipped.add(node.name)
                    continue

                execution_counts[node.name] += 1
                stage.execution_count = execution_counts[node.name]
                await session.commit()

                coro = _execute_single_stage(
                    session, task, stage, stage_index,
                    prior_outputs, compression, project_memory_store,
                    repo_context, stage_defs, worktree_path, sandbox_info,
                    sandbox_required_error=sandbox_required_error,
                )
                tasks_async[node.name] = (stage, asyncio.create_task(coro))

            for name, (stage, atask) in tasks_async.items():
                try:
                    result = await atask
                except Exception as e:
                    logger.exception("Graph parallel stage %s failed", name)
                    await mark_stage_failed(session, task, stage, str(e), error=e)
                    failed.add(name)
                    continue

                if result is None:
                    failed.add(name)
                    continue

                completed.add(name)
                prior_outputs.append({"stage": name, "output": result})
                compressed = await _compress_with_log(task, stage, result)
                if compressed is not None:
                    compression.add(compressed)
                if stage.output_structured:
                    structured_outputs[name] = stage.output_structured

    if iteration >= max_iterations:
        await _fail_task(
            session, task,
            f"Graph execution exceeded max iterations ({max_iterations})",
        )


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
    gate_rejection_context: Optional[Dict[str, str]] = None,
    sandbox_required_error: Optional[str] = None,
) -> Optional[str]:
    """Execute a single stage with model routing and retry context.

    Returns output text on success, None on failure (task is already marked failed).
    """
    # Resolve per-stage overrides from template definition
    sdef = stage_defs.get(stage.stage_name, {})
    stage_model = sdef.get("model")  # None if not specified
    custom_instruction = sdef.get("instruction")  # Phase 1.4
    stage_timeout = sdef.get("timeout")  # Phase 1.4
    evaluator_config = sdef.get("evaluator")  # Phase 2.2

    # Build project memory for the current role
    project_memory: Optional[str] = None
    if project_memory_store:
        try:
            project_memory = project_memory_store.get_memory_for_role(stage.agent_role)
        except Exception:
            logger.warning("Failed to load memory for role %s", stage.agent_role, exc_info=True)

    # Build compressed prior context via sliding window
    # Phase 1.5: Cross-stage context recall — override compression for specified stages
    context_from = sdef.get("context_from")
    full_context_stages = set(context_from) if context_from else None
    try:
        compressed_prior = compression.build_prior_context(
            stage_index, full_context_stages=full_context_stages,
        )
    except TypeError:
        # Backward-compatible for stubs/legacy implementations without full_context_stages.
        compressed_prior = compression.build_prior_context(stage_index)

    # Build retry context if this stage previously failed (smart retry)
    retry_context: Optional[Dict[str, str]] = None
    if stage.error_message or stage.output_summary:
        # Stage has prior failure info — inject it for smarter retry
        if stage.error_message:
            if settings.SKILL_REFLECTION_ENABLED:
                try:
                    from app.worker.failure import generate_structured_reflection
                    reflection = await generate_structured_reflection(
                        error_message=stage.error_message,
                        stage_output=stage.output_summary or "",
                        stage_name=stage.stage_name,
                        agent_role=stage.agent_role,
                    )
                    retry_context = {
                        "error": reflection.get("root_cause", stage.error_message),
                        "lesson": reflection.get("lesson", ""),
                        "suggestion": reflection.get("suggestion", ""),
                        "prior_output": (stage.output_summary or "")[:2000],
                    }
                    # Persist lesson to project memory
                    if (
                        reflection.get("lesson")
                        and settings.MEMORY_ENABLED
                        and task.project_id
                    ):
                        try:
                            from app.worker.memory import MemoryEntry, ProjectMemoryStore
                            store = ProjectMemoryStore(str(task.project_id))
                            entry = MemoryEntry.create(
                                content=reflection["lesson"],
                                source_task_id=str(task.id),
                                source_task_title=task.title,
                                confidence=0.7,
                                tags=["auto-reflection", stage.stage_name],
                            )
                            await store.add_entries("issues", [entry])
                        except Exception:
                            logger.warning(
                                "Failed to persist reflection to memory", exc_info=True
                            )
                except Exception:
                    logger.warning(
                        "Structured reflection failed, using raw error", exc_info=True
                    )
                    retry_context = {
                        "error": stage.error_message,
                        "prior_output": (stage.output_summary or "")[:2000],
                    }
            else:
                retry_context = {
                    "error": stage.error_message,
                    "prior_output": (stage.output_summary or "")[:2000],
                }

    # Determine working directory: worktree for code-producing roles, tmpdir otherwise
    _CODE_ROLES = {"coding", "test"}
    effective_workdir = worktree_path if (worktree_path and stage.agent_role in _CODE_ROLES) else None
    fallback_mode = _resolve_sandbox_fallback_mode()

    # Route to sandbox container or in-process execution
    use_sandbox = sandbox_info is not None and stage.agent_role in _CODE_ROLES

    if (
        settings.SANDBOX_ENABLED
        and stage.agent_role in _CODE_ROLES
        and not use_sandbox
        and fallback_mode == "strict"
    ):
        reason = sandbox_required_error or "sandbox_unavailable"
        error_msg = f"Sandbox unavailable in strict mode: {reason}"
        logger.error(
            "Strict sandbox mode blocked stage %s for task %s: %s",
            stage.stage_name,
            task.id,
            error_msg,
        )
        await mark_stage_failed(session, task, stage, error_msg)
        from app.worker.agents import close_agents_for_task

        close_agents_for_task(str(task.id))
        await _fail_task(session, task, f"Stage {stage.stage_name} failed: {error_msg}")
        return None

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
                custom_instruction=custom_instruction,
                gate_rejection_context=gate_rejection_context,
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
                custom_instruction=custom_instruction,
                gate_rejection_context=gate_rejection_context,
                stage_timeout=stage_timeout,
                evaluator_config=evaluator_config,
            )
        return output
    except Exception as e:
        error_msg = str(e)
        logger.exception("Stage %s failed for task %s", stage.stage_name, task.id)
        await mark_stage_failed(session, task, stage, error_msg, error=e)
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


async def _route_decision(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    routing_config: dict,
    structured_outputs: Dict[str, dict],
) -> Optional[str]:
    """Phase 3.3: LLM-driven dynamic routing after a stage.

    routing_config: {"options": [{"target": "code", "description": "..."}, ...]}

    Returns the name of the next stage to route to, or None to continue normally.
    """
    if not settings.DYNAMIC_ROUTING_ENABLED:
        return None

    options = routing_config.get("options", [])
    if not options:
        return None

    # Build routing prompt
    stage_output = stage.output_summary or ""
    structured = structured_outputs.get(stage.stage_name, {})

    options_text = "\n".join(
        f"- {opt['target']}: {opt.get('description', '')}"
        for opt in options
    )

    try:
        from app.integration.llm_client import ChatMessage, get_llm_client

        model = settings.DYNAMIC_ROUTING_MODEL or settings.LLM_MODEL
        client = get_llm_client()

        prompt = (
            f"你是一个任务路由决策器。根据【{stage.stage_name}】阶段的产出，"
            f"决定下一步应该执行哪个阶段。\n\n"
            f"阶段产出摘要: {structured.get('summary', stage_output[:500])}\n"
            f"状态: {structured.get('status', 'unknown')}\n"
            f"信心分数: {structured.get('confidence', 'N/A')}\n\n"
            f"可选的下一阶段:\n{options_text}\n\n"
            f"请只回复目标阶段名称（如 code、test 等），不要添加任何其他内容。"
        )

        resp = await client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model,
            temperature=0.1,
            max_tokens=50,
        )

        decision = resp.content.strip().lower()

        # Validate decision against options
        valid_targets = {opt["target"] for opt in options}
        if decision not in valid_targets:
            logger.warning(
                "Routing decision '%s' not in valid options %s",
                decision, valid_targets,
            )
            return None

        # Record decision for auditability
        routing_decisions = task.routing_decisions or []
        if isinstance(routing_decisions, list):
            routing_decisions.append({
                "after_stage": stage.stage_name,
                "decision": decision,
                "options": [opt["target"] for opt in options],
                "structured_context": {
                    "status": structured.get("status"),
                    "confidence": structured.get("confidence"),
                },
            })
            task.routing_decisions = routing_decisions
            await session.commit()

        logger.info(
            "Routing decision after stage %s: → %s",
            stage.stage_name, decision,
        )
        return decision

    except Exception:
        logger.warning(
            "Dynamic routing failed after stage %s, continuing normally",
            stage.stage_name, exc_info=True,
        )
        return None


async def _check_interactive_planning(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    stage_output: str,
) -> bool:
    """Phase 3.2: Check if we should pause for interactive plan review after parse stage.

    Returns True if the task was paused (caller should return), False to continue.
    """
    if not settings.INTERACTIVE_PLANNING_ENABLED:
        return False

    # Only trigger after parse stage
    if stage.stage_name != "parse":
        return False

    # Check if template is in the interactive planning list
    allowed_templates = {t.strip() for t in settings.INTERACTIVE_PLANNING_TEMPLATES.split(",") if t.strip()}
    if task.template and task.template.name not in allowed_templates:
        return False

    # Store the plan from parse output
    try:
        plan_data = {"raw_output": stage_output[:5000], "stage": "parse"}
        task.plan = plan_data
    except Exception:
        pass

    # Set task to planning status
    task.status = "planning"
    await session.commit()

    await _safe_broadcast(TASK_STATUS_CHANGED, {
        "task_id": task.id,
        "status": "planning",
    })

    # Create a plan review gate
    gate = HumanGateModel(
        gate_type="plan_review",
        task_id=task.id,
        agent_role="orchestrator",
        status="pending",
        content={
            "stage": "parse",
            "summary": stage_output[:500] if stage_output else "",
            "type": "plan_review",
        },
    )
    session.add(gate)
    await session.commit()
    await session.refresh(gate)

    await _safe_broadcast(GATE_CREATED, {
        "gate_id": gate.id,
        "task_id": task.id,
        "gate_type": "plan_review",
        "stage_name": "parse",
    })
    await notify_gate_created(gate.id, task.id, "parse", "plan_review")

    logger.info("Task %s paused for plan review (gate_id=%s)", task.id, gate.id)

    # Poll for plan approval
    gate_start = datetime.now(timezone.utc)
    while _running:
        await asyncio.sleep(settings.WORKER_GATE_POLL_INTERVAL)
        elapsed = (datetime.now(timezone.utc) - gate_start).total_seconds()
        if elapsed > settings.WORKER_GATE_MAX_WAIT_SECONDS:
            await _fail_task(session, task, "Plan review timed out")
            return True
        try:
            await session.refresh(gate)
        except Exception:
            continue

        if gate.status == "approved":
            # Resume execution
            task.status = "running"
            await session.commit()
            await _safe_broadcast(TASK_STATUS_CHANGED, {
                "task_id": task.id, "status": "running",
            })
            return False  # Continue execution

        if gate.status in ("rejected", "revised"):
            # If revised, update the plan; either way, re-run the parse stage
            if gate.review_comment:
                # Store revision feedback for next parse execution
                stage.error_message = None
                stage.output_summary = None
                stage.status = "pending"
                await session.commit()
            task.status = "running"
            await session.commit()
            return False  # Will be re-executed in next iteration

        if await _is_cancelled(session, task.id):
            return True

    return True


def _should_skip_stage(
    stage: TaskStageModel,
    stage_defs: Dict[str, dict],
    structured_outputs: Dict[str, dict],
) -> bool:
    """Phase 2.1: Check if a stage should be skipped based on conditions."""
    sdef = stage_defs.get(stage.stage_name, {})
    condition = sdef.get("condition")
    if not condition:
        return False

    try:
        from app.worker.conditions import evaluate_condition
        should_execute = evaluate_condition(condition, structured_outputs)
        if not should_execute:
            logger.info(
                "Stage %s skipped: condition not met (%s)",
                stage.stage_name, condition,
            )
            return True
    except Exception:
        logger.warning(
            "Failed to evaluate condition for stage %s, executing anyway",
            stage.stage_name, exc_info=True,
        )
    return False


async def _maybe_insert_dynamic_gate(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    stage_output: str,
) -> bool:
    """Phase 2.3: Insert dynamic gate when confidence is below threshold.

    Returns True if a dynamic gate was inserted and approved.
    """
    if not settings.DYNAMIC_GATE_ENABLED:
        return False

    structured = stage.output_structured
    if not structured or not isinstance(structured, dict):
        return False

    confidence = structured.get("confidence", 1.0)
    if confidence >= settings.DYNAMIC_GATE_CONFIDENCE_THRESHOLD:
        return False

    logger.info(
        "Stage %s confidence %.2f below threshold %.2f, inserting dynamic gate",
        stage.stage_name, confidence, settings.DYNAMIC_GATE_CONFIDENCE_THRESHOLD,
    )

    gate = HumanGateModel(
        gate_type="confidence_review",
        task_id=task.id,
        agent_role=stage.agent_role,
        status="pending",
        content={
            "stage": stage.stage_name,
            "summary": stage_output[:500] if stage_output else "",
            "confidence": str(confidence),
        },
        is_dynamic=True,
    )
    session.add(gate)
    await session.commit()
    await session.refresh(gate)

    await _safe_broadcast(GATE_CREATED, {
        "gate_id": gate.id,
        "task_id": task.id,
        "gate_type": "confidence_review",
        "stage_name": stage.stage_name,
        "is_dynamic": True,
    })
    await notify_gate_created(gate.id, task.id, stage.stage_name, "confidence_review")

    # Poll until resolved (reuse existing gate polling logic)
    gate_start = datetime.now(timezone.utc)
    while _running:
        await asyncio.sleep(settings.WORKER_GATE_POLL_INTERVAL)
        elapsed = (datetime.now(timezone.utc) - gate_start).total_seconds()
        if elapsed > settings.WORKER_GATE_MAX_WAIT_SECONDS:
            return False
        try:
            await session.refresh(gate)
        except Exception:
            continue
        if gate.status in ("approved", "revised"):
            return True
        if gate.status == "rejected":
            return False
        if await _is_cancelled(session, task.id):
            return False
    return False


async def _handle_gate_with_retry(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    gate_def: dict,
    stage_output: str,
    stage_index: int,
    prior_outputs: List[Dict[str, str]],
    compression: CompressionResult,
    project_memory_store,
    repo_context: Optional[str],
    stage_defs: Dict[str, dict],
    worktree_path: Optional[str] = None,
    sandbox_info=None,
    sandbox_required_error: Optional[str] = None,
) -> Optional[str]:
    """Handle gate with retry loop for rejected gates (Phase 1.3).

    Returns the final stage output on success, or None if the task should fail.
    """
    gate_type = gate_def["type"]
    max_retries = gate_def.get("max_retries", 0)
    current_retry = 0

    while True:
        gate_result = await _handle_gate(
            session, task, stage, gate_type, stage_output,
            max_retries=max_retries, current_retry=current_retry,
        )
        result_status = gate_result["result"]

        if result_status == "approved":
            return stage_output

        if result_status == "revised":
            # Phase 2.4: re-execute with revision context
            gate_rejection_ctx = {
                "comment": gate_result.get("comment", ""),
                "revised_content": gate_result.get("revised_content", ""),
                "retry": f"{current_retry + 1}/{max_retries}",
            }
            current_retry += 1
            # Reset stage for re-execution
            stage.status = "pending"
            stage.output_summary = None
            stage.error_message = None
            await session.commit()
            new_output = await _execute_single_stage(
                session, task, stage, stage_index,
                prior_outputs, compression, project_memory_store,
                repo_context, stage_defs, worktree_path, sandbox_info,
                gate_rejection_context=gate_rejection_ctx,
                sandbox_required_error=sandbox_required_error,
            )
            if new_output is None:
                return None
            stage_output = new_output
            # Update prior_outputs with new result
            for po in prior_outputs:
                if po["stage"] == stage.stage_name:
                    po["output"] = new_output
                    break
            continue

        if result_status == "rejected":
            if current_retry < max_retries:
                # Phase 1.3: Re-execute with rejection feedback
                logger.info(
                    "Gate rejected for stage %s, retry %d/%d with feedback",
                    stage.stage_name, current_retry + 1, max_retries,
                )
                gate_rejection_ctx = {
                    "comment": gate_result.get("comment", ""),
                    "retry": f"{current_retry + 1}/{max_retries}",
                }
                current_retry += 1
                # Reset stage for re-execution
                stage.status = "pending"
                stage.output_summary = None
                stage.error_message = None
                await session.commit()
                new_output = await _execute_single_stage(
                    session, task, stage, stage_index,
                    prior_outputs, compression, project_memory_store,
                    repo_context, stage_defs, worktree_path, sandbox_info,
                    gate_rejection_context=gate_rejection_ctx,
                    sandbox_required_error=sandbox_required_error,
                )
                if new_output is None:
                    return None
                stage_output = new_output
                # Update prior_outputs with new result
                for po in prior_outputs:
                    if po["stage"] == stage.stage_name:
                        po["output"] = new_output
                        break
                continue
            else:
                # No retries left — fail the task
                await _fail_task(
                    session, task,
                    f"Gate rejected after stage {stage.stage_name} "
                    f"(exhausted {max_retries} retries)",
                )
                return None

        # timeout, cancelled, or other — fail
        await _fail_task(
            session, task,
            f"Gate {result_status} after stage {stage.stage_name}",
        )
        return None


async def _handle_gate(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    gate_type: str,
    stage_output: str,
    max_retries: int = 0,
    current_retry: int = 0,
) -> dict:
    """Create a gate and poll until it is approved, rejected, or revised.

    Returns dict with keys:
      - "result": "approved" | "rejected" | "revised" | "timeout" | "cancelled"
      - "comment": reviewer's comment (if rejected/revised)
      - "revised_content": revision text (if revised, Phase 2.4)
      - "retry_count": current retry number
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
    # Check if a pending gate already exists for this task and stage
    result = await session.execute(
        select(HumanGateModel)
        .where(
            HumanGateModel.task_id == task.id,
            HumanGateModel.gate_type == gate_type,
            HumanGateModel.status == "pending",
        )
    )
    existing_gate = None
    for row in result.scalars().all():
        if row.content and row.content.get("stage") == stage.stage_name:
            existing_gate = row
            break

    if existing_gate:
        gate = existing_gate
        logger.info(
            "Found existing pending gate after stage %s (gate_id=%s)",
            stage.stage_name, gate.id,
        )
    else:
        gate = HumanGateModel(
            gate_type=gate_type,
            task_id=task.id,
            agent_role=stage.agent_role,
            status="pending",
            content={
                "stage": stage.stage_name,
                "summary": stage_output[:500] if stage_output else "",
            },
            retry_count=current_retry,
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
            return {"result": "timeout", "comment": "", "retry_count": current_retry}

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
            return {"result": "approved", "comment": gate.review_comment or "", "retry_count": current_retry}
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
            return {
                "result": "rejected",
                "comment": gate.review_comment or "",
                "retry_count": current_retry,
            }
        elif gate.status == "revised":
            # Phase 2.4: Gate "revise and continue" mode
            logger.info("Gate %s revised", gate.id)
            duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                stage=stage,
                event_type="gate_wait_revised",
                status="success",
                correlation_id=gate_corr,
                duration_ms=duration_ms,
                response_body={"gate_id": gate.id},
            )
            await _close_started_system_log(
                started_log_id=gate_started_log_id,
                started_at_monotonic=gate_started_at,
                status="success",
                result="gate_revised",
            )
            return {
                "result": "revised",
                "comment": gate.review_comment or "",
                "revised_content": gate.revised_content or "",
                "retry_count": current_retry,
            }

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
            return {"result": "cancelled", "comment": "", "retry_count": current_retry}

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
    return {"result": "cancelled", "comment": "", "retry_count": current_retry}


async def _is_cancelled(session: AsyncSession, task_id: str) -> bool:
    """Re-read task status from DB to check for cancellation."""
    result = await session.execute(
        select(TaskModel.status).where(TaskModel.id == task_id)
    )
    status = result.scalar_one_or_none()
    return status == "cancelled"


def _parse_gates(task: TaskModel) -> Dict[str, dict]:
    """Parse gate definitions from template.gates JSON.

    Returns {after_stage_name: {"type": gate_type, "max_retries": int}}.
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
        max_retries = g.get("max_retries", settings.GATE_DEFAULT_MAX_RETRIES)
        if after_stage:
            result[after_stage] = {"type": gate_type, "max_retries": max_retries}
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
