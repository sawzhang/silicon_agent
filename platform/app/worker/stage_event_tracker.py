"""Stage lifecycle event tracking: chats, turns, tool calls."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from app.websocket.events import TASK_LOG_STREAM_UPDATE
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers (also re-exported by executor.py)
# ---------------------------------------------------------------------------

_TOOL_FAILURE_PREFIXES = (
    "Error:",
    "Error (exit",
    "Error reading file:",
    "Error writing file:",
    "Exit code:",
)


def infer_tool_status(output: str) -> str:
    return "failed" if output.startswith(_TOOL_FAILURE_PREFIXES) else "success"


async def _safe_broadcast(event: str, data: dict) -> None:
    """Broadcast a WebSocket event, swallowing any errors."""
    import app.worker.executor as _executor

    try:
        await _executor.ws_manager.broadcast(event, data)
    except Exception:
        logger.warning("WS broadcast failed for event %s, ignoring", event, exc_info=True)


def _summarize_tool_command(tool_name: str, args: dict[str, Any]) -> str:
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


# ---------------------------------------------------------------------------
# Tool-activity classification (used by StageEventTracker)
# ---------------------------------------------------------------------------

_EXPLORATION_EXECUTE_PREFIXES = (
    "ls ",
    "find ",
    "pwd",
    "cat ",
    "head ",
    "tail ",
    "tree",
    "rg ",
)
_VERIFICATION_EXECUTE_MARKERS = (
    "pytest",
    "unittest",
    "gradlew test",
    "gradlew build",
    "gradlew testclasses",
    "mvn test",
    "npm test",
    "pnpm test",
    "yarn test",
    "go test",
    "cargo test",
)


def _classify_tool_activity(tool_name: str, args: dict[str, Any]) -> str:
    normalized_tool = (tool_name or "").strip().lower()
    if normalized_tool in {"write", "edit"}:
        return "implementation"
    if normalized_tool == "read":
        return "exploration"

    if normalized_tool in {"execute", "execute_script"}:
        command = str(args.get("command") or "").strip().lower()
        if any(marker in command for marker in _VERIFICATION_EXECUTE_MARKERS):
            return "verification"
        if any(command.startswith(prefix) for prefix in _EXPLORATION_EXECUTE_PREFIXES):
            return "exploration"

    return "other"


# ---------------------------------------------------------------------------
# StageEventTracker – encapsulates mutable tracking state and event helpers
# ---------------------------------------------------------------------------

class StageEventTracker:
    """Tracks and emits stage-level lifecycle events (chats, turns, tool calls)."""

    def __init__(
        self,
        pipeline: Any,
        task_id: str,
        stage_id: str,
        stage_name: str,
        agent_role: str,
    ) -> None:
        self.task_id = task_id
        self.stage_id = stage_id
        self.stage_name = stage_name
        self.agent_role = agent_role

        self._pipeline = pipeline
        self._tool_runs: dict[str, dict[str, Any]] = {}
        self._chat_runs: dict[str, dict[str, Any]] = {}
        self._turn_runs: dict[str, dict[str, Any]] = {}
        self._active_chat_correlation_id: Optional[str] = None
        self._handler_source = f"stage-log:{task_id}:{stage_id}:{uuid.uuid4().hex}"
        self._instrumented_runners: list[Any] = []
        self._instrumented_runner_ids: set[int] = set()
        self._completed_tool_runs: list[dict[str, str]] = []
        self._exploration_actions = 0
        self._implementation_actions = 0
        self._verification_attempts = 0
        self._verification_failures = 0
        self._successful_verifications = 0
        self._forced_convergence_used = False

    # -- public emit helpers --------------------------------------------------

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

    async def emit_chat_sent(self, *, request_body: dict[str, Any]) -> str:
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
            request_body=request_body,
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
                updates={
                    "status": status,
                    "duration_ms": effective_duration,
                },
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

    def get_completed_tool_runs(self) -> list[dict[str, str]]:
        return list(self._completed_tool_runs)

    def should_force_convergence(self) -> bool:
        if self._forced_convergence_used:
            return False

        normalized = self.stage_name.strip().lower()
        if normalized == "code":
            return self._implementation_actions == 0 and self._exploration_actions >= 4
        if normalized == "test":
            if self._verification_failures > 0 and self._successful_verifications == 0:
                return True
            return self._verification_attempts == 0 and self._exploration_actions >= 3
        return False

    def mark_forced_convergence_used(self) -> None:
        self._forced_convergence_used = True

    def record_tool_activity(self, tool_name: str, args: dict[str, Any], status: str) -> None:
        activity = _classify_tool_activity(tool_name, args)
        if activity == "exploration":
            self._exploration_actions += 1
            return
        if activity == "implementation":
            self._implementation_actions += 1
            return
        if activity == "verification":
            self._verification_attempts += 1
            if status == "success":
                self._successful_verifications += 1
            else:
                self._verification_failures += 1

    # -- runner event registration -------------------------------------------

    def register_runner_events(self, current_runner: Any) -> None:
        rid = id(current_runner)
        if rid in self._instrumented_runner_ids:
            return
        self._instrumented_runner_ids.add(rid)
        self._instrumented_runners.append(current_runner)
        fallback_workspace = getattr(current_runner, "default_cwd", None)

        # Capture self into a local so inner closures are concise
        tracker = self

        async def _on_turn_start(event: Any) -> None:
            chat_correlation = tracker._active_chat_correlation_id
            if not chat_correlation:
                return
            turn = int(getattr(event, "turn", 0))
            correlation = f"{chat_correlation}:turn:{turn}"
            log_id = await tracker._pipeline.emit_create(
                task_id=tracker.task_id,
                stage_id=tracker.stage_id,
                stage_name=tracker.stage_name,
                agent_role=tracker.agent_role,
                event_type="llm_turn_sent",
                event_source="llm",
                status="running",
                correlation_id=correlation,
                request_body={
                    "turn": turn,
                    "message_count": int(getattr(event, "message_count", 0)),
                },
            )
            tracker._turn_runs[correlation] = {
                "log_id": log_id,
                "started": time.monotonic(),
            }

        async def _on_turn_end(event: Any) -> None:
            chat_correlation = tracker._active_chat_correlation_id
            if not chat_correlation:
                return
            turn = int(getattr(event, "turn", 0))
            correlation = f"{chat_correlation}:turn:{turn}"
            duration_ms: Optional[float] = None
            run_info = tracker._turn_runs.get(correlation)
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                await tracker._pipeline.emit_update(
                    log_id=run_info["log_id"],
                    updates={
                        "status": "success",
                        "duration_ms": duration_ms,
                    },
                    priority="high",
                )
                tracker._turn_runs.pop(correlation, None)
            await tracker._pipeline.emit_create(
                task_id=tracker.task_id,
                stage_id=tracker.stage_id,
                stage_name=tracker.stage_name,
                agent_role=tracker.agent_role,
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

            log_id = await tracker._pipeline.emit_create(
                task_id=tracker.task_id,
                stage_id=tracker.stage_id,
                stage_name=tracker.stage_name,
                agent_role=tracker.agent_role,
                event_type="tool_call_executed",
                event_source="tool",
                status="running",
                correlation_id=tool_call_id,
                command=_summarize_tool_command(tool_name, args),
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                execution_mode="in_process",
                missing_fields=missing_fields,
                priority="high",
            )
            tracker._tool_runs[tool_call_id] = {
                "log_id": log_id,
                "started": time.monotonic(),
                "summary": "",
                "truncated": False,
                "command": _summarize_tool_command(tool_name, args),
            }

        async def _on_tool_execution_update(event: Any) -> None:
            tool_call_id = str(getattr(event, "tool_call_id", ""))
            if not tool_call_id:
                return
            chunk = str(getattr(event, "output", ""))
            run_info = tracker._tool_runs.get(tool_call_id)
            if run_info is not None:
                summary, truncated = _append_output_summary(run_info["summary"], chunk)
                run_info["summary"] = summary
                run_info["truncated"] = run_info["truncated"] or truncated
                await _safe_broadcast(
                    TASK_LOG_STREAM_UPDATE,
                    {
                        "task_id": tracker.task_id,
                        "stage_id": tracker.stage_id,
                        "stage_name": tracker.stage_name,
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
            tracker.record_tool_activity(tool_name, args, status)

            run_info = tracker._tool_runs.get(tool_call_id)
            duration_ms: Optional[float] = None
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                output_summary = run_info["summary"] or output
                await tracker._pipeline.emit_update(
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
                tracker._completed_tool_runs.append(
                    {
                        "status": status,
                        "command": str(run_info.get("command") or _summarize_tool_command(tool_name, args)),
                        "result_preview": output_summary,
                    }
                )
                if len(tracker._completed_tool_runs) > 20:
                    tracker._completed_tool_runs = tracker._completed_tool_runs[-20:]
                await _safe_broadcast(
                    TASK_LOG_STREAM_UPDATE,
                    {
                        "task_id": tracker.task_id,
                        "stage_id": tracker.stage_id,
                        "stage_name": tracker.stage_name,
                        "log_id": run_info["log_id"],
                        "tool_call_id": tool_call_id,
                        "chunk": "",
                        "finished": True,
                        "status": status,
                    },
                )
                tracker._tool_runs.pop(tool_call_id, None)
                return

            workspace = args.get("cwd") or fallback_workspace
            missing_fields: list[str] = []
            if not workspace:
                missing_fields.append("workspace")
            await tracker._pipeline.emit_create(
                task_id=tracker.task_id,
                stage_id=tracker.stage_id,
                stage_name=tracker.stage_name,
                agent_role=tracker.agent_role,
                event_type="tool_call_executed",
                event_source="tool",
                status=status,
                correlation_id=tool_call_id,
                command=_summarize_tool_command(tool_name, args),
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                execution_mode="in_process",
                duration_ms=duration_ms,
                result=output,
                output_summary=output,
                missing_fields=missing_fields,
                priority="high",
            )
            tracker._completed_tool_runs.append(
                {
                    "status": status,
                    "command": _summarize_tool_command(tool_name, args),
                    "result_preview": output,
                }
            )
            if len(tracker._completed_tool_runs) > 20:
                tracker._completed_tool_runs = tracker._completed_tool_runs[-20:]

        current_runner.events.on("turn_start", _on_turn_start, source=self._handler_source)
        current_runner.events.on("turn_end", _on_turn_end, source=self._handler_source)
        current_runner.events.on(
            "before_tool_call", _on_before_tool_call, source=self._handler_source
        )
        current_runner.events.on(
            "tool_execution_update",
            _on_tool_execution_update,
            source=self._handler_source,
        )
        current_runner.events.on(
            "after_tool_result", _on_after_tool_result, source=self._handler_source
        )

    # -- finalize & detach ---------------------------------------------------

    async def finalize_unfinished(self, status: str, reason: str) -> None:
        """Close out any in-flight turns, chats, and tool runs."""
        # 1. turns
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

        # 2. chats
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

        # 3. tools
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
            await _safe_broadcast(
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

    def detach_all_handlers(self) -> None:
        for instrumented in self._instrumented_runners:
            try:
                instrumented.events.off_by_source(self._handler_source)
            except Exception:
                logger.warning("Failed to detach stage log handlers", exc_info=True)
