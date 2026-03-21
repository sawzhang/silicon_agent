"""Workspace lifecycle: worktree setup, runtime workspace, finalization, and cleanup."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.task import TaskModel, TaskStageModel

logger = logging.getLogger(__name__)


async def _has_git_worktree_changes(worktree_path: Optional[str]) -> Optional[bool]:
    """Return whether worktree has uncommitted changes; None when check cannot be performed."""
    if not worktree_path:
        return None
    try:
        proc = await asyncio.create_subprocess_shell(
            "git status --porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=worktree_path,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return bool(stdout.decode().strip())
    except Exception:
        logger.warning("Failed to verify git changes for worktree %s", worktree_path, exc_info=True)
        return None


async def _ensure_code_stage_has_changes(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    worktree_path: Optional[str],
) -> bool:
    """Code stage must produce repository changes; otherwise fail fast."""
    from app.worker.engine import (
        _fail_task, _has_git_worktree_changes, mark_stage_failed,
    )

    if (stage.stage_name or "").lower() != "code":
        return True
    # When worktree mode is disabled/unavailable, skip git diff verification.
    # Some tasks still complete via sandbox workspace edits without a git worktree.
    if not worktree_path:
        logger.info(
            "Skip code-stage git change verification for task %s: worktree not enabled/available",
            task.id,
        )
        return True

    changed = await _has_git_worktree_changes(worktree_path)
    if changed:
        return True

    reason = (
        "Code stage produced no repository file changes."
        if changed is False
        else "Code stage change verification failed (worktree unavailable or git status failed)."
    )
    logger.error("Task %s code stage has no detectable changes: %s", task.id, reason)
    await mark_stage_failed(session, task, stage, reason)
    from app.worker.agents import close_agents_for_task

    close_agents_for_task(str(task.id))
    await _fail_task(session, task, f"Stage {stage.stage_name} failed: {reason}")
    return False


async def _setup_worktree(task: TaskModel) -> tuple[Optional[str], Any]:
    """Create git worktree for the task if enabled. Returns (worktree_path, worktree_mgr)."""
    from app.worker.engine import (
        Path, _close_started_system_log, _emit_system_log,
        ensure_repo_local_mirror, get_worktree_manager,
    )

    if not (settings.WORKTREE_ENABLED and task.project):
        return None, None

    repo_local_path = (task.project.repo_local_path or "").strip()
    repo_path_source = "project_config"
    repo_url = (task.project.repo_url or "").strip()
    if repo_local_path and not Path(repo_local_path).exists():
        logger.warning(
            "Configured repo_local_path is invalid for project %s: %s",
            task.project.id,
            repo_local_path,
        )
        repo_local_path = ""
        repo_path_source = "invalid_config_repaired"
        task.project.repo_local_path = None
    if not repo_local_path:
        if not repo_url:
            return None, None
        repo_local_path = await ensure_repo_local_mirror(
            project_id=str(task.project.id),
            repo_url=repo_url,
            base_branch=task.project.branch or "main",
        ) or ""
        if not repo_local_path:
            logger.warning(
                "Failed to prepare local mirror for task %s from repo_url=%s",
                task.id,
                repo_url,
            )
            return None, None
        repo_path_source = "auto_cloned" if repo_path_source == "project_config" else repo_path_source
        task.project.repo_local_path = repo_local_path

    worktree_path: Optional[str] = None
    worktree_mgr = get_worktree_manager(repo_local_path)
    worktree_corr = f"worktree-create-{uuid.uuid4().hex}"
    worktree_started_at = time.monotonic()
    worktree_started_log_id = await _emit_system_log(
        task,
        event_type="worktree_create_started",
        status="running",
        correlation_id=worktree_corr,
        response_body={
            "repo_local_path": repo_local_path,
            "repo_path_source": repo_path_source,
            "repo_url": task.project.repo_url,
        },
    )
    try:
        worktree_path = await worktree_mgr.create_worktree(
            task_id=str(task.id),
            task_title=task.title,
            base_branch=task.project.branch or "main",
            target_branch=task.target_branch,
        )
        duration_ms = round((time.monotonic() - worktree_started_at) * 1000, 2)
        if worktree_path:
            logger.info("Task %s using worktree: %s", task.id, worktree_path)
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
        else:
            await _emit_system_log(
                task,
                event_type="worktree_create_finished",
                status="failed",
                correlation_id=worktree_corr,
                response_body={"error": "worktree_path_unavailable"},
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=worktree_started_log_id,
                started_at_monotonic=worktree_started_at,
                status="failed",
                result="worktree_path_unavailable",
            )
            return None, None
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


async def _prepare_runtime_workspace(
    task: TaskModel,
    worktree_path: Optional[str],
) -> tuple[Optional[str], str, Optional[str]]:
    """Resolve task workspace once before stages start.

    Returns (workspace_path, workspace_source, branch_name).
    """
    from app.worker.engine import Path, prepare_workspace_from_repo, tempfile

    if worktree_path:
        return worktree_path, "worktree", getattr(task, "target_branch", None)

    workspace_path = Path(tempfile.gettempdir()) / "silicon_agent" / "tasks" / str(task.id)
    workspace_path.mkdir(parents=True, exist_ok=True)
    target_branch = (getattr(task, "target_branch", None) or "").strip() or None

    repo_url = (task.project.repo_url or "").strip() if task.project else ""
    if settings.WORKTREE_ENABLED and repo_url:
        return None, "worktree_required", None

    if not repo_url:
        return str(workspace_path), "tmp_empty", target_branch

    prepared, branch_name, error = await prepare_workspace_from_repo(
        workspace=str(workspace_path),
        repo_url=repo_url,
        task_id=str(task.id),
        task_title=task.title,
        base_branch=(task.project.branch if task.project else None) or "main",
        target_branch=target_branch,
    )
    if not prepared:
        logger.error(
            "Failed to prepare fallback repo workspace for task %s: %s",
            task.id,
            error or "unknown",
        )
        return None, "tmp_clone_failed", None
    return str(workspace_path), "tmp_cloned", branch_name


async def _finalize_task_resources(
    session: AsyncSession,
    task: TaskModel,
    prior_outputs: List[Dict[str, str]],
    project_memory_store: Any,
    worktree_mgr: Any,
    worktree_path: Optional[str],
    workspace_path: Optional[str],
    workspace_source: str,
    workspace_branch: Optional[str],
    sandbox_mgr: Any,
    sandbox_info: Any,
) -> bool:
    """Post-completion: extract memories, commit/push worktree, cleanup resources."""
    from app.worker.engine import (
        _close_started_system_log, _emit_system_log, _fail_task,
        commit_and_push_workspace, create_pr_for_workspace,
    )

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

    # SCM finalize: commit, push, and create PR when repo is configured
    repo_url = (task.project.repo_url or "").strip() if task.project else ""
    if repo_url and workspace_path:
        worktree_commit_corr = f"worktree-commit-{uuid.uuid4().hex}"
        worktree_commit_started_at = time.monotonic()
        worktree_commit_started_log_id = await _emit_system_log(
            task,
            event_type="worktree_commit_push_started",
            status="running",
            correlation_id=worktree_commit_corr,
            response_body={"workspace_path": workspace_path},
        )
        try:
            if worktree_mgr and worktree_path:
                branch = await worktree_mgr.commit_and_push(
                    task_id=str(task.id),
                    commit_message=f"feat: {task.title}\n\nTask-ID: {task.id}",
                    target_branch=task.target_branch,
                )
            else:
                branch = await commit_and_push_workspace(
                    workspace=workspace_path,
                    commit_message=f"feat: {task.title}\n\nTask-ID: {task.id}",
                    target_branch=task.target_branch or workspace_branch,
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
            if branch:
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
                issue_num = getattr(task, "github_issue_number", None)
                if issue_num:
                    pr_body += f"\n\nFixes #{issue_num}"
                if worktree_mgr and worktree_path:
                    pr_url = await worktree_mgr.create_pr(
                        task_id=str(task.id),
                        title=task.title,
                        body=pr_body,
                        base_branch=task.project.branch or "main",
                    )
                else:
                    pr_url = await create_pr_for_workspace(
                        workspace=workspace_path,
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
        except Exception as exc:
            logger.warning("Worktree commit/push failed for task %s", task.id, exc_info=True)
            duration_ms = round((time.monotonic() - worktree_commit_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                event_type="worktree_commit_push_finished",
                status="failed",
                correlation_id=worktree_commit_corr,
                response_body={"error": "worktree_commit_push_failed", "detail": str(exc)},
                duration_ms=duration_ms,
            )
            await _close_started_system_log(
                started_log_id=worktree_commit_started_log_id,
                started_at_monotonic=worktree_commit_started_at,
                status="failed",
                result="worktree_commit_push_failed",
            )
            await _fail_task(session, task, "Worktree commit/push failed")
            return False

    await _cleanup_runtime_resources(
        task,
        worktree_mgr,
        worktree_path,
        workspace_path,
        workspace_source,
        sandbox_mgr,
        sandbox_info,
    )
    return True


async def _cleanup_runtime_resources(
    task: TaskModel,
    worktree_mgr: Any,
    worktree_path: Optional[str],
    workspace_path: Optional[str],
    workspace_source: str,
    sandbox_mgr: Any,
    sandbox_info: Any,
) -> None:
    """Always cleanup runtime resources regardless of success/failure paths."""
    from app.worker.engine import (
        Path, _close_started_system_log, _emit_system_log, shutil, tempfile,
    )

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

    # Cleanup temporary task workspace (non-worktree execution path)
    if workspace_path and workspace_source.startswith("tmp_"):
        try:
            tmp_workspace = Path(workspace_path).resolve()
            tmp_root = (Path(tempfile.gettempdir()) / "silicon_agent" / "tasks").resolve()
            if tmp_workspace.is_relative_to(tmp_root):
                shutil.rmtree(tmp_workspace, ignore_errors=True)
        except Exception:
            logger.warning("Temporary workspace cleanup failed for task %s", task.id, exc_info=True)

    # Clean up sandbox containers (both task-level and per-role)
    if sandbox_mgr:
        try:
            if hasattr(sandbox_mgr, "destroy_role_sandboxes"):
                await sandbox_mgr.destroy_role_sandboxes(str(task.id))
            elif sandbox_info:
                await sandbox_mgr.destroy(str(task.id))
        except Exception:
            logger.warning("Sandbox cleanup failed for task %s", task.id, exc_info=True)


def _build_repo_context(project) -> str:
    """Build a text block describing the project's repo for agent prompt injection."""
    parts = []
    if project.tech_stack:
        parts.append(f"### 技术栈\n{', '.join(project.tech_stack)}")
    if project.repo_tree:
        tree = project.repo_tree
        _REPO_TREE_MAX_CHARS = 3000
        if len(tree) > _REPO_TREE_MAX_CHARS:
            tree = tree[:_REPO_TREE_MAX_CHARS] + "\n...(目录树已截断)"
        parts.append(f"### 目录结构\n{tree}")
    if project.repo_url:
        branch = project.branch or "main"
        parts.append(f"### 仓库\n{project.repo_url} (branch: {branch})")
    return "\n\n".join(parts)
