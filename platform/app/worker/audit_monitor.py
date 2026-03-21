"""Audit, monitoring, and circuit breaker: system logging, stage audit, cost control."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integration.event_collector import event_collector
from app.models.audit import CircuitBreakerModel
from app.models.task import TaskModel, TaskStageModel
from app.services.task_log_pipeline import get_task_log_pipeline
from app.websocket.events import CB_TRIGGERED
# ws_manager imported lazily from engine for monkeypatch compat
# compress_stage_output imported lazily from engine for monkeypatch compat

logger = logging.getLogger(__name__)


async def _safe_broadcast(event: str, data: dict) -> None:
    """Broadcast a WebSocket event, swallowing any errors."""
    from app.worker.engine import ws_manager

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


async def _compress_with_log(
    task: TaskModel,
    stage: TaskStageModel,
    output: str,
) -> Any | None:
    import app.worker.engine as _engine

    correlation_id = f"compression-{uuid.uuid4().hex}"
    compression_started_at = time.monotonic()
    compression_started_log_id = await _engine._emit_system_log(
        task,
        stage=stage,
        event_type="compression_started",
        status="running",
        correlation_id=correlation_id,
        response_body={"output_length": len(output or "")},
    )
    try:
        compressed = await _engine.compress_stage_output(stage.stage_name, output)
    except Exception as exc:
        duration_ms = round((time.monotonic() - compression_started_at) * 1000, 2)
        await _engine._emit_system_log(
            task,
            stage=stage,
            event_type="compression_finished",
            status="failed",
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            response_body={"error": str(exc)},
        )
        await _engine._close_started_system_log(
            started_log_id=compression_started_log_id,
            started_at_monotonic=compression_started_at,
            status="failed",
            result=str(exc),
        )
        return None

    duration_ms = round((time.monotonic() - compression_started_at) * 1000, 2)
    await _engine._emit_system_log(
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
    await _engine._close_started_system_log(
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
    import app.worker.engine as _engine

    if task.total_tokens <= _engine.settings.CB_MAX_TOKENS_PER_TASK and \
       task.total_cost_rmb <= _engine.settings.CB_MAX_COST_PER_TASK_RMB:
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
    await _engine._safe_broadcast(CB_TRIGGERED, {
        "task_id": task.id,
        "reason": reason,
        "total_tokens": task.total_tokens,
        "total_cost_rmb": task.total_cost_rmb,
    })
    from app.worker.agents import close_agents_for_task
    close_agents_for_task(str(task.id))
    await _engine._fail_task(session, task, reason)
    return True
