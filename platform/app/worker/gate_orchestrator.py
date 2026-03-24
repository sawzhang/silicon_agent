"""Gate orchestration: human approval gates, dynamic gates, and interactive planning."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.websocket.events import GATE_CREATED, TASK_STATUS_CHANGED
from app.worker.compressor import CompressionResult

logger = logging.getLogger(__name__)


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
    workspace_path: Optional[str] = None,
    sandbox_info=None,
    sandbox_required_error: Optional[str] = None,
) -> Optional[str]:
    """Handle gate with retry loop for rejected gates (Phase 1.3).

    Returns the final stage output on success, or None if the task should fail.
    """
    from app.worker.engine import _execute_single_stage, _fail_task, _handle_gate

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
                repo_context, stage_defs, workspace_path, sandbox_info,
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
                    repo_context, stage_defs, workspace_path, sandbox_info,
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
    from app.worker.engine import (
        _close_started_system_log,
        _emit_system_log,
        _is_cancelled,
        _running,
        _safe_broadcast,
        notify_gate_created,
    )

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
    # 1) Reuse existing pending gate for this task/stage first.
    pending_result = await session.execute(
        select(HumanGateModel)
        .where(
            HumanGateModel.task_id == task.id,
            HumanGateModel.gate_type == gate_type,
            HumanGateModel.status == "pending",
        )
    )
    existing_gate = None
    for row in pending_result.scalars().all():
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
        # 2) Idempotency: if this stage has already been approved after the latest
        # completion, do not create a duplicate pending gate during resume/recovery.
        latest_result = await session.execute(
            select(HumanGateModel)
            .where(
                HumanGateModel.task_id == task.id,
                HumanGateModel.gate_type == gate_type,
            )
            .order_by(HumanGateModel.created_at.desc())
        )
        latest_gate_for_stage = None
        for row in latest_result.scalars().all():
            if row.content and row.content.get("stage") == stage.stage_name:
                latest_gate_for_stage = row
                break

        if (
            latest_gate_for_stage is not None
            and latest_gate_for_stage.status == "approved"
            and (
                stage.completed_at is None
                or latest_gate_for_stage.reviewed_at is None
                or latest_gate_for_stage.reviewed_at >= stage.completed_at
            )
        ):
            duration_ms = round((time.monotonic() - gate_started_at) * 1000, 2)
            await _emit_system_log(
                task,
                stage=stage,
                event_type="gate_wait_approved",
                status="success",
                correlation_id=gate_corr,
                duration_ms=duration_ms,
                response_body={
                    "gate_id": latest_gate_for_stage.id,
                    "already_approved": True,
                },
            )
            await _close_started_system_log(
                started_log_id=gate_started_log_id,
                started_at_monotonic=gate_started_at,
                status="success",
                result="gate_already_approved",
            )
            logger.info(
                "Reuse approved gate for stage %s (gate_id=%s)",
                stage.stage_name,
                latest_gate_for_stage.id,
            )
            return {
                "result": "approved",
                "comment": latest_gate_for_stage.review_comment or "",
                "retry_count": current_retry,
            }

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


async def _maybe_insert_dynamic_gate(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    stage_output: str,
) -> bool:
    """Phase 2.3: Insert dynamic gate when confidence is below threshold.

    Returns True if a dynamic gate was inserted and approved.
    """
    from app.worker.engine import _is_cancelled, _running, _safe_broadcast, notify_gate_created

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


async def _check_interactive_planning(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    stage_output: str,
) -> bool:
    """Phase 3.2: Check if we should pause for interactive plan review after parse stage.

    Returns True if the task was paused (caller should return), False to continue.
    """
    from app.worker.engine import (
        _fail_task,
        _is_cancelled,
        _running,
        _safe_broadcast,
        notify_gate_created,
    )

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
