"""Stage event tracking for structured logging during stage execution.

Encapsulates mutable tracking state (chats, turns, tool calls) and event handler
registration for AgentRunners, extracted from executor.execute_stage.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from app.websocket.events import TASK_LOG_STREAM_UPDATE

logger = logging.getLogger(__name__)

_TOOL_FAILURE_PREFIXES = (
    "Error:",
    "Error (exit",
    "Error reading file:",
    "Error writing file:",
    "Exit code:",
)


def infer_tool_status(output: str) -> str:
    """Determine tool call status from output prefix."""
    return "failed" if output.startswith(_TOOL_FAILURE_PREFIXES) else "success"


def summarize_tool_command(tool_name: str, args: dict[str, Any]) -> str:
    """Build a short description of a tool invocation."""
    if tool_name == "execute":
        return str(args.get("command") or "").strip() or "execute"
    if tool_name == "execute_script":
        return "execute_script"
    if tool_name == "read":
        path = str(args.get("path") or "").strip()
        return f"read {path}".strip()
    if tool_name == "write":
        path = str(args.get("path") or "").strip()
        return f"write {path}".strip()
    if tool_name == "edit":
        path = str(args.get("path") or "").strip()
        return f"edit {path}".strip()
    if tool_name == "skill":
        skill_name = str(args.get("name") or "").strip()
        return f"skill:{skill_name}" if skill_name else "skill"
    return tool_name or "tool"


def _append_output_summary(existing: str, chunk: str) -> tuple[str, bool]:
    """Append chunk to output summary, truncating at 50KB."""
    marker = "\n...[truncated]"
    if not chunk:
        return existing, False
    if existing.endswith(marker) and len(existing) >= 50_000:
        return existing, True
    merged = f"{existing}{chunk}" if existing else chunk
    if len(merged) <= 50_000:
        return merged, False
    keep_len = max(0, 50_000 - len(marker))
    return merged[:keep_len] + marker, True


class StageEventTracker:
    """Tracks and emits structured events (chats, turns, tool calls) during stage execution.

    Encapsulates mutable tracking state and event handler registration for AgentRunners.
    """

    def __init__(
        self,
        task_id: str,
        stage_id: str,
        stage_name: str,
        agent_role: str,
        pipeline: Any,
        broadcast_fn: Any,
    ) -> None:
        self.task_id = task_id
        self.stage_id = stage_id
        self.stage_name = stage_name
        self.agent_role = agent_role
        self._pipeline = pipeline
        self._broadcast = broadcast_fn

        self._active_chat_correlation_id: Optional[str] = None
        self._tool_runs: dict[str, dict[str, Any]] = {}
        self._chat_runs: dict[str, dict[str, Any]] = {}
        self._turn_runs: dict[str, dict[str, Any]] = {}

        self._handler_source = f"stage-log:{task_id}:{stage_id}:{uuid.uuid4().hex}"
        self._instrumented_runners: list[Any] = []
        self._instrumented_runner_ids: set[int] = set()

    # ---- Direct event emission ----

    async def emit_system_event(
        self,
        event_type: str,
        *,
        status: str,
        response_body: Optional[dict[str, Any]] = None,
        result: Optional[str] = None,
        priority: str = "normal",
    ) -> None:
        await self._pipeline.emit_create(
            task_id=self.task_id,
            stage_id=self.stage_id,
            stage_name=self.stage_name,
            agent_role=self.agent_role,
            event_type=event_type,
            event_source="system",
            status=status,
            response_body=response_body,
            result=result,
            priority=priority,  # type: ignore[arg-type]
        )

    async def emit_chat_sent(self, *, prompt: str, metadata: dict[str, Any]) -> str:
        correlation_id = f"chat-{uuid.uuid4().hex}"
        self._active_chat_correlation_id = correlation_id
        log_id = await self._pipeline.emit_create(
            task_id=self.task_id,
            stage_id=self.stage_id,
            stage_name=self.stage_name,
            agent_role=self.agent_role,
            event_type="agent_runner_chat_sent",
            event_source="llm",
            status="running",
            correlation_id=correlation_id,
            request_body={"prompt": prompt, **metadata},
            priority="high",
        )
        self._chat_runs[correlation_id] = {
            "log_id": log_id,
            "started": time.monotonic(),
        }
        return correlation_id

    async def emit_chat_received(
        self,
        correlation_id: str,
        *,
        status: str,
        response_body: dict[str, Any],
        duration_ms: float,
    ) -> None:
        run_info = self._chat_runs.get(correlation_id)
        effective_duration = duration_ms
        if run_info is not None:
            effective_duration = round((time.monotonic() - run_info["started"]) * 1000, 2)
            await self._pipeline.emit_update(
                log_id=run_info["log_id"],
                updates={"status": status, "duration_ms": effective_duration},
                priority="high",
            )
            self._chat_runs.pop(correlation_id, None)
        await self._pipeline.emit_create(
            task_id=self.task_id,
            stage_id=self.stage_id,
            stage_name=self.stage_name,
            agent_role=self.agent_role,
            event_type="agent_runner_chat_received",
            event_source="llm",
            status=status,
            correlation_id=correlation_id,
            response_body=response_body,
            duration_ms=effective_duration,
            priority="high",
        )
        if self._active_chat_correlation_id == correlation_id:
            self._active_chat_correlation_id = None

    # ---- Runner event registration ----

    def register_runner(self, runner: Any) -> None:
        """Register event handlers on a runner for tool/turn tracking."""
        rid = id(runner)
        if rid in self._instrumented_runner_ids:
            return
        self._instrumented_runner_ids.add(rid)
        self._instrumented_runners.append(runner)
        fallback_workspace = getattr(runner, "default_cwd", None)

        async def _on_turn_start(event: Any) -> None:
            chat_correlation = self._active_chat_correlation_id
            if not chat_correlation:
                return
            turn = int(getattr(event, "turn", 0))
            correlation = f"{chat_correlation}:turn:{turn}"
            log_id = await self._pipeline.emit_create(
                task_id=self.task_id,
                stage_id=self.stage_id,
                stage_name=self.stage_name,
                agent_role=self.agent_role,
                event_type="llm_turn_sent",
                event_source="llm",
                status="running",
                correlation_id=correlation,
                request_body={
                    "turn": turn,
                    "message_count": int(getattr(event, "message_count", 0)),
                },
            )
            self._turn_runs[correlation] = {
                "log_id": log_id,
                "started": time.monotonic(),
            }

        async def _on_turn_end(event: Any) -> None:
            chat_correlation = self._active_chat_correlation_id
            if not chat_correlation:
                return
            turn = int(getattr(event, "turn", 0))
            correlation = f"{chat_correlation}:turn:{turn}"
            duration_ms: Optional[float] = None
            run_info = self._turn_runs.get(correlation)
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                await self._pipeline.emit_update(
                    log_id=run_info["log_id"],
                    updates={"status": "success", "duration_ms": duration_ms},
                    priority="high",
                )
                self._turn_runs.pop(correlation, None)
            await self._pipeline.emit_create(
                task_id=self.task_id,
                stage_id=self.stage_id,
                stage_name=self.stage_name,
                agent_role=self.agent_role,
                event_type="llm_turn_received",
                event_source="llm",
                status="success",
                correlation_id=correlation,
                response_body={
                    "turn": turn,
                    "has_tool_calls": bool(getattr(event, "has_tool_calls", False)),
                    "tool_call_count": int(getattr(event, "tool_call_count", 0)),
                    "content": str(getattr(event, "content", "")),
                },
                duration_ms=duration_ms,
            )

        async def _on_before_tool_call(event: Any) -> None:
            tool_call_id = str(getattr(event, "tool_call_id", ""))
            if not tool_call_id:
                return
            tool_name = str(getattr(event, "tool_name", ""))
            args = getattr(event, "args", {}) or {}
            if not isinstance(args, dict):
                args = {}
            workspace = args.get("cwd") or fallback_workspace
            missing_fields: list[str] = []
            if not workspace:
                missing_fields.append("workspace")

            log_id = await self._pipeline.emit_create(
                task_id=self.task_id,
                stage_id=self.stage_id,
                stage_name=self.stage_name,
                agent_role=self.agent_role,
                event_type="tool_call_executed",
                event_source="tool",
                status="running",
                correlation_id=tool_call_id,
                command=summarize_tool_command(tool_name, args),
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                missing_fields=missing_fields,
                priority="high",
            )
            self._tool_runs[tool_call_id] = {
                "log_id": log_id,
                "started": time.monotonic(),
                "summary": "",
                "truncated": False,
            }

        async def _on_tool_execution_update(event: Any) -> None:
            tool_call_id = str(getattr(event, "tool_call_id", ""))
            if not tool_call_id:
                return
            chunk = str(getattr(event, "output", ""))
            run_info = self._tool_runs.get(tool_call_id)
            if run_info is not None:
                summary, truncated = _append_output_summary(run_info["summary"], chunk)
                run_info["summary"] = summary
                run_info["truncated"] = run_info["truncated"] or truncated
                await self._broadcast(
                    TASK_LOG_STREAM_UPDATE,
                    {
                        "task_id": self.task_id,
                        "stage_id": self.stage_id,
                        "stage_name": self.stage_name,
                        "log_id": run_info["log_id"],
                        "tool_call_id": tool_call_id,
                        "chunk": chunk,
                        "finished": False,
                    },
                )

        async def _on_after_tool_result(event: Any) -> None:
            tool_call_id = str(getattr(event, "tool_call_id", ""))
            tool_name = str(getattr(event, "tool_name", ""))
            args = getattr(event, "args", {}) or {}
            if not isinstance(args, dict):
                args = {}
            output = str(getattr(event, "result", ""))
            status = infer_tool_status(output)

            run_info = self._tool_runs.get(tool_call_id)
            duration_ms: Optional[float] = None
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                output_summary = run_info["summary"] or output
                await self._pipeline.emit_update(
                    log_id=run_info["log_id"],
                    updates={
                        "status": status,
                        "duration_ms": duration_ms,
                        "result": output,
                        "output_summary": output_summary,
                        "output_truncated": run_info["truncated"],
                    },
                    priority="high",
                )
                await self._broadcast(
                    TASK_LOG_STREAM_UPDATE,
                    {
                        "task_id": self.task_id,
                        "stage_id": self.stage_id,
                        "stage_name": self.stage_name,
                        "log_id": run_info["log_id"],
                        "tool_call_id": tool_call_id,
                        "chunk": "",
                        "finished": True,
                        "status": status,
                    },
                )
                self._tool_runs.pop(tool_call_id, None)
                return

            workspace = args.get("cwd") or fallback_workspace
            missing_fields: list[str] = []
            if not workspace:
                missing_fields.append("workspace")
            await self._pipeline.emit_create(
                task_id=self.task_id,
                stage_id=self.stage_id,
                stage_name=self.stage_name,
                agent_role=self.agent_role,
                event_type="tool_call_executed",
                event_source="tool",
                status=status,
                correlation_id=tool_call_id,
                command=summarize_tool_command(tool_name, args),
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                duration_ms=duration_ms,
                result=output,
                output_summary=output,
                missing_fields=missing_fields,
                priority="high",
            )

        runner.events.on("turn_start", _on_turn_start, source=self._handler_source)
        runner.events.on("turn_end", _on_turn_end, source=self._handler_source)
        runner.events.on(
            "before_tool_call", _on_before_tool_call, source=self._handler_source,
        )
        runner.events.on(
            "tool_execution_update",
            _on_tool_execution_update,
            source=self._handler_source,
        )
        runner.events.on(
            "after_tool_result", _on_after_tool_result, source=self._handler_source,
        )

    # ---- Cleanup ----

    def detach_all_handlers(self) -> None:
        """Remove all registered event handlers from runners."""
        for instrumented in self._instrumented_runners:
            try:
                instrumented.events.off_by_source(self._handler_source)
            except Exception:
                logger.warning("Failed to detach stage log handlers", exc_info=True)

    async def finalize_unfinished(self, status: str, reason: str) -> None:
        """Close out any unfinished turn/chat/tool tracking records."""
        # Turns
        for correlation, info in list(self._turn_runs.items()):
            await self._pipeline.emit_update(
                log_id=info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": round((time.monotonic() - info["started"]) * 1000, 2),
                    "result": reason,
                },
                priority="high",
            )
            self._turn_runs.pop(correlation, None)

        # Chats
        self._active_chat_correlation_id = None
        for correlation, info in list(self._chat_runs.items()):
            await self._pipeline.emit_update(
                log_id=info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": round((time.monotonic() - info["started"]) * 1000, 2),
                    "result": reason,
                },
                priority="high",
            )
            self._chat_runs.pop(correlation, None)

        # Tools
        for tool_call_id, info in list(self._tool_runs.items()):
            await self._pipeline.emit_update(
                log_id=info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": round((time.monotonic() - info["started"]) * 1000, 2),
                    "result": reason,
                    "output_summary": info.get("summary") or "",
                    "output_truncated": bool(info.get("truncated")),
                },
                priority="high",
            )
            await self._broadcast(
                TASK_LOG_STREAM_UPDATE,
                {
                    "task_id": self.task_id,
                    "stage_id": self.stage_id,
                    "stage_name": self.stage_name,
                    "log_id": info["log_id"],
                    "tool_call_id": tool_call_id,
                    "chunk": "",
                    "finished": True,
                    "status": status,
                },
            )
            self._tool_runs.pop(tool_call_id, None)
