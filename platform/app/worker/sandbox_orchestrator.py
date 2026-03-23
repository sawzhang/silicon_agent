"""Sandbox orchestration: container lifecycle for task execution."""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.models.task import TaskModel

logger = logging.getLogger(__name__)


def _get_settings():
    """Get settings via engine module to support monkeypatching."""
    import app.worker.engine as _engine
    return _engine.settings


def _resolve_sandbox_fallback_mode() -> str:
    s = _get_settings()
    raw = (s.SANDBOX_FALLBACK_MODE or "graceful").strip().lower()
    return raw if raw in {"graceful", "strict"} else "graceful"


def _resolve_sandbox_workspace(
    task_id: str,
    workspace_path: Optional[str],
    workspace_source: str,
) -> tuple[str, str]:
    if workspace_path:
        return workspace_path, workspace_source
    s = _get_settings()
    return str(Path(s.SANDBOX_WORKSPACE_BASE_DIR) / task_id), "fallback"


def _get_sandbox_roles() -> set[str]:
    """Return the set of roles that should use sandbox execution.

    Reads from ``SANDBOX_ROLES`` setting (JSON list).  Falls back to
    ``{"coding", "test"}`` if the setting is missing or malformed.
    """
    s = _get_settings()
    try:
        return set(json.loads(s.SANDBOX_ROLES))
    except Exception:
        return {"coding", "test"}


async def _setup_role_sandbox(
    task: TaskModel,
    role: str,
    workspace_path: Optional[str],
    workspace_source_hint: str,
) -> tuple[Any, Any, Optional[str]]:
    """Create or reuse sandbox for a specific role in a task.

    Returns ``(sandbox_info, sandbox_mgr, sandbox_required_error)``.
    """
    s = _get_settings()
    if not s.SANDBOX_ENABLED:
        return None, None, None
    sandbox_roles = _get_sandbox_roles()
    if role not in sandbox_roles:
        return None, None, None

    from app.worker.sandbox import get_sandbox_manager  # noqa: PLC0415

    sandbox_mgr = get_sandbox_manager()

    resolved_workspace, workspace_source = _resolve_sandbox_workspace(
        str(task.id),
        workspace_path,
        workspace_source_hint,
    )

    sandbox_image = None
    project = getattr(task, "project", None)
    if project and getattr(project, "sandbox_image", None):
        sandbox_image = project.sandbox_image

    try:
        result = await sandbox_mgr.get_or_create_role_sandbox(
            str(task.id),
            role,
            workspace=resolved_workspace,
            workspace_source=workspace_source,
            image=sandbox_image,
        )
    except Exception as exc:
        logger.warning(
            "Role sandbox creation failed for task %s role %s: %s",
            task.id, role, exc,
        )
        return None, sandbox_mgr, f"role_sandbox_create_failed: {exc}"

    if result.info:
        return result.info, sandbox_mgr, None
    return None, sandbox_mgr, f"{result.error_code}: {result.error_message}"


async def _setup_sandbox(
    task: TaskModel,
    workspace_path: Optional[str],
    workspace_source_hint: str,
) -> tuple[Any, Any, Optional[str]]:
    """Create sandbox container if enabled.

    Returns (sandbox_info, sandbox_mgr, sandbox_required_error).
    """
    s = _get_settings()
    if not s.SANDBOX_ENABLED:
        return None, None, None

    from app.worker.engine import _close_started_system_log, _emit_system_log
    from app.worker.sandbox import SandboxCreateResult, get_sandbox_manager

    sandbox_mgr = get_sandbox_manager()
    sandbox_image = None
    if task.project and task.project.sandbox_image:
        sandbox_image = task.project.sandbox_image
    fallback_mode = _resolve_sandbox_fallback_mode()
    resolved_workspace, workspace_source = _resolve_sandbox_workspace(
        str(task.id),
        workspace_path,
        workspace_source_hint,
    )

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
        workspace_prepare_error_code = "workspace_not_found"
        workspace_prepare_error = (
            f"Workspace path does not exist or is not directory: {resolved_workspace}"
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
            "image": sandbox_image or s.SANDBOX_IMAGE,
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
