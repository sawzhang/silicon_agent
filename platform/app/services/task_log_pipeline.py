from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.config import settings
from app.db.session import async_session_factory
from app.services.task_log_service import TaskLogService

logger = logging.getLogger(__name__)

Priority = Literal["high", "normal", "low"]


@dataclass(slots=True)
class _LogOperation:
    op_type: Literal["create", "update"]
    payload: dict[str, Any]
    priority: Priority
    enqueued_at: float


class TaskLogEventPipeline:
    def __init__(
        self,
        queue_size: int = 4000,
        flush_interval_seconds: float = 1.0,
        batch_size: int = 200,
    ) -> None:
        self._queue: asyncio.Queue[_LogOperation] = asyncio.Queue(maxsize=queue_size)
        self._flush_interval_seconds = flush_interval_seconds
        self._batch_size = batch_size
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._sequence_lock = asyncio.Lock()
        self._sequence_cache: dict[str, int] = {}

    async def start(self) -> None:
        if self._running:
            return
        async with self._start_lock:
            if self._running:
                return
            self._running = True
            self._worker_task = asyncio.create_task(self._run(), name="task-log-pipeline")
            logger.info(
                "Task log pipeline started (queue_size=%d, flush_interval=%.2fs, batch_size=%d)",
                self._queue.maxsize,
                self._flush_interval_seconds,
                self._batch_size,
            )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._worker_task is not None:
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        logger.info("Task log pipeline stopped")

    async def ensure_started(self) -> None:
        if self._running:
            return
        await self.start()

    async def wait_until_drained(self, timeout_seconds: float = 5.0) -> None:
        await self.ensure_started()
        await asyncio.wait_for(self._queue.join(), timeout=timeout_seconds)

    async def emit_create(
        self,
        *,
        task_id: str,
        stage_id: Optional[str],
        stage_name: str,
        agent_role: Optional[str],
        event_type: str,
        event_source: str,
        status: str,
        request_body: Optional[dict[str, Any]] = None,
        response_body: Optional[dict[str, Any]] = None,
        command: Optional[str] = None,
        command_args: Optional[dict[str, Any]] = None,
        workspace: Optional[str] = None,
        execution_mode: Optional[str] = None,
        duration_ms: Optional[float] = None,
        result: Optional[str] = None,
        output_summary: Optional[str] = None,
        missing_fields: Optional[list[str]] = None,
        correlation_id: Optional[str] = None,
        log_id: Optional[str] = None,
        priority: Priority = "normal",
    ) -> str:
        await self.ensure_started()

        normalized_stage_id = str(stage_id) if stage_id else None
        item_id = log_id or str(uuid.uuid4())
        item = {
            "id": item_id,
            "task_id": str(task_id),
            "stage_id": normalized_stage_id,
            "stage_name": stage_name,
            "agent_role": agent_role,
            "correlation_id": correlation_id,
            "event_seq": await self._next_event_seq(str(task_id), normalized_stage_id),
            "event_type": event_type,
            "event_source": event_source,
            "status": status,
            "request_body": request_body,
            "response_body": response_body,
            "command": command,
            "command_args": command_args,
            "workspace": workspace,
            "execution_mode": execution_mode,
            "duration_ms": duration_ms,
            "result": result,
            "output_summary": output_summary,
            "missing_fields": missing_fields or [],
        }
        await self._enqueue(_LogOperation("create", item, priority, time.monotonic()))
        return item_id

    async def emit_update(
        self,
        *,
        log_id: str,
        updates: dict[str, Any],
        priority: Priority = "normal",
    ) -> bool:
        await self.ensure_started()
        op = _LogOperation(
            op_type="update",
            payload={"log_id": log_id, "updates": dict(updates)},
            priority=priority,
            enqueued_at=time.monotonic(),
        )
        return await self._enqueue(op)

    async def _enqueue(self, op: _LogOperation) -> bool:
        try:
            self._queue.put_nowait(op)
            return True
        except asyncio.QueueFull:
            if op.priority == "low":
                logger.warning(
                    "Task log pipeline queue full; dropped low-priority %s operation",
                    op.op_type,
                )
                return False

            # Preserve critical ordering by waiting for queue capacity instead of bypassing queue.
            await self._queue.put(op)
            return True

    async def _run(self) -> None:
        while self._running or not self._queue.empty():
            batch: list[_LogOperation] = []
            try:
                first = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._flush_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

            batch.append(first)
            while len(batch) < self._batch_size and not self._queue.empty():
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            try:
                await self._flush_batch(batch)
            except Exception:
                logger.exception("Task log pipeline batch flush failed")
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _flush_batch(self, batch: list[_LogOperation]) -> None:
        if not batch:
            return

        async with async_session_factory() as session:
            service = TaskLogService(session)
            for op in batch:
                if op.op_type == "create":
                    await service.create_log(op.payload)
                elif op.op_type == "update":
                    await service.update_log(
                        op.payload["log_id"],
                        op.payload.get("updates") or {},
                    )
            await session.commit()

    async def _next_event_seq(self, task_id: str, _stage_id: Optional[str]) -> int:
        key = task_id
        async with self._sequence_lock:
            if key not in self._sequence_cache:
                async with async_session_factory() as session:
                    service = TaskLogService(session)
                    self._sequence_cache[key] = await service.get_max_event_seq(task_id)
            self._sequence_cache[key] += 1
            return self._sequence_cache[key]


_task_log_pipeline = TaskLogEventPipeline(
    queue_size=settings.TASK_LOG_PIPELINE_QUEUE_SIZE,
    flush_interval_seconds=settings.TASK_LOG_PIPELINE_FLUSH_INTERVAL_SECONDS,
    batch_size=settings.TASK_LOG_PIPELINE_BATCH_SIZE,
)


def get_task_log_pipeline() -> TaskLogEventPipeline:
    return _task_log_pipeline


async def start_task_log_pipeline() -> None:
    await _task_log_pipeline.start()


async def stop_task_log_pipeline() -> None:
    await _task_log_pipeline.stop()
