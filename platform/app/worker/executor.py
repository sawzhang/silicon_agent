"""Single-stage executor: build prompt -> AgentRunner chat -> update DB -> broadcast events."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import AgentModel
from app.models.task import TaskModel, TaskStageModel
from app.services.task_log_pipeline import get_task_log_pipeline
from app.websocket.events import AGENT_STATUS_CHANGED, TASK_LOG_STREAM_UPDATE, TASK_STAGE_UPDATE
from app.websocket.manager import ws_manager
from app.worker.agents import get_agent, get_agent_text_only
from app.worker.prompts import StageContext, build_user_prompt

logger = logging.getLogger(__name__)


_TOOL_FAILURE_PREFIXES = (
    "Error:",
    "Error (exit",
    "Error reading file:",
    "Error writing file:",
    "Exit code:",
)


def _int_or_none(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _build_runtime_overrides(
    agent: AgentModel | None,
    stage_model: Optional[str],
) -> dict[str, Any]:
    config = dict(agent.config or {}) if agent and isinstance(agent.config, dict) else {}
    extra_dirs_raw = config.get("extra_skill_dirs")
    extra_skill_dirs = (
        [str(item) for item in extra_dirs_raw if isinstance(item, str)]
        if isinstance(extra_dirs_raw, list)
        else None
    )
    return {
        "model": stage_model or (agent.model_name if agent else None),
        "max_turns": _int_or_none(config.get("max_turns")),
        "extra_skill_dirs": extra_skill_dirs,
        "system_prompt_append": config.get("system_prompt_append")
        if isinstance(config.get("system_prompt_append"), str)
        else None,
        "temperature": _float_or_none(config.get("temperature")),
        "max_tokens": _int_or_none(config.get("max_tokens")),
    }


def _chat_kwargs_for_runner(runner: Any, runtime_overrides: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    try:
        sig = inspect.signature(runner.chat)
    except (TypeError, ValueError):
        return kwargs
    if "temperature" in sig.parameters and runtime_overrides.get("temperature") is not None:
        kwargs["temperature"] = runtime_overrides["temperature"]
    if "max_tokens" in sig.parameters and runtime_overrides.get("max_tokens") is not None:
        kwargs["max_tokens"] = runtime_overrides["max_tokens"]
    return kwargs


def infer_tool_status(output: str) -> str:
    return "failed" if output.startswith(_TOOL_FAILURE_PREFIXES) else "success"


async def _safe_broadcast(event: str, data: dict) -> None:
    """Broadcast a WebSocket event, swallowing any errors."""
    try:
        await ws_manager.broadcast(event, data)
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
# Module-level helpers extracted from execute_stage
# ---------------------------------------------------------------------------

_TOOL_CALL_ERROR_PATTERNS = (
    "invalid function arguments",
    "invalid_request_error",
    "tool_use_failed",
    "function_call",
)


def _is_tool_call_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(p in msg for p in _TOOL_CALL_ERROR_PATTERNS)


def _clear_current_task_cancellation_state() -> None:
    current = asyncio.current_task()
    if current is None or not hasattr(current, "uncancel"):
        return
    while current.cancelling():
        current.uncancel()


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


# ---------------------------------------------------------------------------
# Extracted helpers: continuations and stage success
# ---------------------------------------------------------------------------

async def _handle_continuations(
    runner: Any,
    output: str,
    runtime_overrides: dict[str, Any],
    tracker: StageEventTracker,
) -> tuple[str, int]:
    """Follow up with continuation prompts when the LLM output was truncated."""
    _MAX_CONTINUATIONS = 3
    _TRUNCATION_SENTINEL = "Max turns reached"
    continuations = 0

    while _TRUNCATION_SENTINEL in (output or "") and continuations < _MAX_CONTINUATIONS:
        continuations += 1
        continuation_started = time.monotonic()
        prompt = "请继续完成上面的输出，从你停下的地方继续。"
        chat_correlation = await tracker.emit_chat_sent(
            request_body={
                "prompt": prompt,
                "model": getattr(getattr(runner, "config", None), "model", None),
                "stage": tracker.stage_name,
                "agent_role": tracker.agent_role,
                "temperature": runtime_overrides.get("temperature"),
                "max_tokens": runtime_overrides.get("max_tokens"),
                "continuation": continuations,
                "timeout_seconds": settings.WORKER_STAGE_TIMEOUT,
            },
        )
        try:
            continuation_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
            cont_response = await asyncio.wait_for(
                runner.chat(prompt, reset=False, **continuation_kwargs),
                timeout=settings.WORKER_STAGE_TIMEOUT,
            )
            cont_text = cont_response.text_content or ""
            await tracker.emit_chat_received(
                chat_correlation,
                status="success",
                response_body={"continuation": continuations, "content": cont_text},
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
            )
            output = output.replace(
                f"[{_TRUNCATION_SENTINEL}. Please continue the conversation.]",
                "",
            ).strip()
            output = f"{output}\n\n{cont_text}".strip()
        except asyncio.CancelledError:
            _clear_current_task_cancellation_state()
            await tracker.emit_chat_received(
                chat_correlation,
                status="cancelled",
                response_body={"continuation": continuations, "error": "cancelled"},
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
            )
            raise
        except Exception as e:
            await tracker.emit_chat_received(
                chat_correlation,
                status="failed",
                response_body={"continuation": continuations, "error": str(e)},
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
            )
            break

    total_tokens = runner.cumulative_usage.total_tokens
    return output, total_tokens


async def _finalize_stage_success(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    agent: AgentModel | None,
    output: str,
    total_tokens: int,
    elapsed: float,
) -> None:
    """Persist completed-stage state to DB and broadcast updates."""
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = round(elapsed, 2)
    stage.tokens_used = total_tokens
    stage.output_summary = output

    # Phase 1.1: Extract structured output from raw text
    try:
        from app.worker.contracts import extract_structured_output
        structured = await extract_structured_output(stage.stage_name, output)
        if structured:
            stage.output_structured = structured
    except Exception:
        logger.warning("Structured extraction failed for stage %s", stage.stage_name, exc_info=True)

    await session.commit()

    task.total_tokens += total_tokens
    cost = total_tokens * settings.CB_TOKEN_PRICE_PER_1K / 1000
    task.total_cost_rmb += cost
    await session.commit()

    await _safe_broadcast(
        TASK_STAGE_UPDATE,
        {
            "task_id": task.id,
            "stage_id": stage.id,
            "stage_name": stage.stage_name,
            "status": "completed",
            "duration_seconds": stage.duration_seconds,
            "tokens_used": total_tokens,
        },
    )

    if agent:
        agent.status = "idle"
        agent.current_task_id = None
        agent.last_active_at = datetime.now(timezone.utc)
        await session.commit()
        await _safe_broadcast(
            AGENT_STATUS_CHANGED,
            {
                "role": agent.role,
                "status": "idle",
                "current_task_id": None,
                "current_stage": None,
            },
        )

    logger.info("Stage %s completed: %.1fs, %d tokens", stage.stage_name, elapsed, total_tokens)


# ---------------------------------------------------------------------------
# Main stage executor
# ---------------------------------------------------------------------------

async def execute_stage(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    prior_outputs: List[Dict[str, str]],
    compressed_outputs: Optional[List[Dict[str, str]]] = None,
    project_memory: Optional[str] = None,
    repo_context: Optional[str] = None,
    retry_context: Optional[Dict[str, str]] = None,
    stage_model: Optional[str] = None,
    workdir_override: Optional[str] = None,
    custom_instruction: Optional[str] = None,
    gate_rejection_context: Optional[Dict[str, str]] = None,
    stage_timeout: Optional[float] = None,
    evaluator_config: Optional[dict] = None,
) -> str:
    """Execute a single stage: call AgentRunner and update DB/broadcast."""
    now = datetime.now(timezone.utc)
    task_id = str(task.id)
    stage_id = str(stage.id)

    stage.status = "running"
    stage.started_at = now
    await session.commit()

    agent = await _get_agent(session, stage.agent_role)
    if agent:
        agent.status = "running"
        agent.current_task_id = task.id
        agent.started_at = now
        agent.last_active_at = now
        await session.commit()
        await _safe_broadcast(
            AGENT_STATUS_CHANGED,
            {
                "role": agent.role,
                "status": "running",
                "current_task_id": task.id,
                "current_stage": stage.stage_name,
            },
        )

    await _safe_broadcast(
        TASK_STAGE_UPDATE,
        {
            "task_id": task.id,
            "stage_id": stage.id,
            "stage_name": stage.stage_name,
            "status": "running",
        },
    )

    start_time = time.monotonic()
    ctx = StageContext(
        task_title=task.title,
        task_description=task.description,
        stage_name=stage.stage_name,
        agent_role=stage.agent_role,
        prior_outputs=prior_outputs,
        compressed_outputs=compressed_outputs,
        project_memory=project_memory,
        repo_context=repo_context,
        retry_context=retry_context,
        custom_instruction=custom_instruction,
        gate_rejection_context=gate_rejection_context,
    )
    user_prompt = build_user_prompt(ctx)

    runtime_overrides = _build_runtime_overrides(agent, stage_model)
    runner = get_agent(
        stage.agent_role,
        task_id,
        model=runtime_overrides["model"],
        max_turns=runtime_overrides["max_turns"],
        extra_skill_dirs=runtime_overrides["extra_skill_dirs"],
        system_prompt_append=runtime_overrides["system_prompt_append"],
    )
    if workdir_override and runner.default_cwd != workdir_override:
        runner.default_cwd = workdir_override
    runner.reset_usage()

    pipeline = get_task_log_pipeline()
    tracker = StageEventTracker(
        pipeline=pipeline,
        task_id=task_id,
        stage_id=stage_id,
        stage_name=stage.stage_name,
        agent_role=stage.agent_role,
    )
    tracker.register_runner_events(runner)

    last_error: BaseException | None = None
    used_text_only_fallback = False
    response: Any | None = None

    try:
        for attempt in range(settings.WORKER_STAGE_MAX_RETRIES + 1):
            llm_started = time.monotonic()
            chat_correlation = await tracker.emit_chat_sent(
                request_body={
                    "prompt": user_prompt,
                    "model": getattr(getattr(runner, "config", None), "model", None),
                    "stage": stage.stage_name,
                    "agent_role": stage.agent_role,
                    "temperature": runtime_overrides.get("temperature"),
                    "max_tokens": runtime_overrides.get("max_tokens"),
                    "attempt": attempt + 1,
                    "timeout_seconds": settings.WORKER_STAGE_TIMEOUT,
                },
            )
            try:
                chat_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
                response = await asyncio.wait_for(
                    runner.chat(user_prompt, reset=True, **chat_kwargs),
                    timeout=settings.WORKER_STAGE_TIMEOUT,
                )
                await tracker.emit_chat_received(
                    chat_correlation,
                    status="success",
                    response_body={"attempt": attempt + 1, "content": response.text_content},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )
                break
            except asyncio.CancelledError as e:
                last_error = e
                _clear_current_task_cancellation_state()
                await tracker.emit_chat_received(
                    chat_correlation,
                    status="cancelled",
                    response_body={"attempt": attempt + 1, "error": "cancelled"},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )
                raise
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(
                    f"Stage {stage.stage_name} LLM call timed out "
                    f"after {settings.WORKER_STAGE_TIMEOUT}s"
                )
                await tracker.emit_chat_received(
                    chat_correlation,
                    status="failed",
                    response_body={"attempt": attempt + 1, "error": str(last_error)},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )
                if attempt >= settings.WORKER_STAGE_MAX_RETRIES:
                    raise last_error
                delay = settings.WORKER_STAGE_RETRY_DELAY * (2 ** attempt)
                await tracker.emit_system_event(
                    "llm_retry_scheduled",
                    status="success",
                    response_body={
                        "attempt": attempt + 1,
                        "next_attempt": attempt + 2,
                        "delay_seconds": delay,
                        "reason": "timeout",
                    },
                )
                await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                await tracker.emit_chat_received(
                    chat_correlation,
                    status="failed",
                    response_body={"attempt": attempt + 1, "error": str(e)},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )

                if _is_tool_call_error(e) and not used_text_only_fallback:
                    await tracker.emit_system_event(
                        "llm_fallback_text_only",
                        status="success",
                        response_body={"attempt": attempt + 1, "reason": str(e)},
                    )
                    runner = get_agent_text_only(
                        stage.agent_role,
                        task_id,
                        model=runtime_overrides["model"],
                        max_turns=runtime_overrides["max_turns"],
                        extra_skill_dirs=runtime_overrides["extra_skill_dirs"],
                        system_prompt_append=runtime_overrides["system_prompt_append"],
                    )
                    if workdir_override and runner.default_cwd != workdir_override:
                        runner.default_cwd = workdir_override
                    runner.reset_usage()
                    tracker.register_runner_events(runner)
                    used_text_only_fallback = True
                    continue

                if attempt < settings.WORKER_STAGE_MAX_RETRIES:
                    delay = settings.WORKER_STAGE_RETRY_DELAY * (2 ** attempt)
                    await tracker.emit_system_event(
                        "llm_retry_scheduled",
                        status="success",
                        response_body={
                            "attempt": attempt + 1,
                            "next_attempt": attempt + 2,
                            "delay_seconds": delay,
                            "reason": str(e),
                        },
                    )
                    await asyncio.sleep(delay)
                else:
                    raise last_error
    finally:
        _clear_current_task_cancellation_state()
        tracker.detach_all_handlers()

        if isinstance(last_error, asyncio.CancelledError):
            finalize_status = "cancelled"
            finalize_reason = "execution cancelled before lifecycle closeout"
        elif last_error is not None:
            finalize_status = "failed"
            finalize_reason = f"execution failed before lifecycle closeout: {last_error}"
        else:
            finalize_status = "cancelled"
            finalize_reason = "execution ended before lifecycle closeout"

        await tracker.finalize_unfinished(finalize_status, finalize_reason)

    if response is None:
        raise RuntimeError(f"Stage {stage.stage_name} returned no response")

    elapsed = time.monotonic() - start_time
    output = response.text_content
    total_tokens = runner.cumulative_usage.total_tokens

    output, total_tokens = await _handle_continuations(
        runner, output, runtime_overrides, tracker
    )

    # Phase 2.2: Evaluator-optimizer loop (if configured for this stage)
    if evaluator_config and evaluator_config.get("enabled"):
        output, total_tokens = await _run_evaluator_loop_inner(
            runner, output, total_tokens, stage, runtime_overrides,
            evaluator_config,
        )

    await _finalize_stage_success(session, task, stage, agent, output, total_tokens, elapsed)
    return output



async def _run_evaluator_loop_inner(
    runner: Any,
    output: str,
    total_tokens: int,
    stage: TaskStageModel,
    runtime_overrides: dict[str, Any],
    evaluator_config: dict,
) -> tuple[str, int]:
    """Inner evaluator loop implementation.

    evaluator_config: {
        "enabled": True,
        "max_iterations": 3,
        "criteria": "代码质量、完整性、可维护性",
        "min_confidence": 0.7,
    }
    """
    max_iterations = evaluator_config.get("max_iterations", settings.EVALUATOR_MAX_ITERATIONS)
    min_confidence = evaluator_config.get("min_confidence", settings.EVALUATOR_DEFAULT_MIN_CONFIDENCE)
    criteria = evaluator_config.get("criteria", "产出质量、完整性、准确性")

    for iteration in range(max_iterations):
        # Step 1: Evaluate current output
        eval_prompt = (
            f"请评估你刚才在【{stage.stage_name}】阶段的产出质量。\n"
            f"评估标准: {criteria}\n\n"
            f"请以 JSON 格式回复（不要添加代码块标记）：\n"
            '{"confidence": 0.0到1.0的信心分数, '
            '"issues": ["发现的问题列表"], '
            '"suggestions": ["改进建议"]}'
        )
        chat_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
        try:
            eval_response = await asyncio.wait_for(
                runner.chat(eval_prompt, reset=False, **chat_kwargs),
                timeout=settings.WORKER_STAGE_TIMEOUT,
            )
            eval_text = eval_response.text_content
            total_tokens = runner.cumulative_usage.total_tokens
        except Exception:
            logger.warning("Evaluator prompt failed for stage %s", stage.stage_name, exc_info=True)
            break

        # Parse confidence
        import json as _json
        confidence = min_confidence  # Default to threshold
        try:
            eval_data = _json.loads(eval_text.strip())
            confidence = float(eval_data.get("confidence", min_confidence))
        except (ValueError, _json.JSONDecodeError):
            logger.warning("Failed to parse evaluator response for stage %s", stage.stage_name)
            break

        # Store self-assessment score
        stage.self_assessment_score = confidence

        logger.info(
            "Evaluator iteration %d for stage %s: confidence=%.2f (min=%.2f)",
            iteration + 1, stage.stage_name, confidence, min_confidence,
        )

        if confidence >= min_confidence:
            break

        # Step 2: Improve based on evaluation
        improve_prompt = (
            f"你的自评信心分数为 {confidence:.2f}（最低要求 {min_confidence:.2f}）。\n"
            f"发现的问题: {eval_text}\n\n"
            "请根据上述评估改进你的产出，输出完整的改进版本。"
        )
        try:
            improve_response = await asyncio.wait_for(
                runner.chat(improve_prompt, reset=False, **chat_kwargs),
                timeout=settings.WORKER_STAGE_TIMEOUT,
            )
            output = improve_response.text_content
            total_tokens = runner.cumulative_usage.total_tokens
        except Exception:
            logger.warning("Improvement prompt failed for stage %s", stage.stage_name, exc_info=True)
            break

    return output, total_tokens


async def mark_stage_failed(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    error_message: str,
    error: Exception | None = None,
) -> None:
    """Mark a stage as failed, classify the failure, and reset the agent."""
    from app.worker.failure import classify_failure

    stage.status = "failed"
    stage.error_message = error_message
    stage.completed_at = datetime.now(timezone.utc)
    # Phase 1.2: Classify the failure for recovery routing
    category = classify_failure(
        error=error,
        error_message=error_message,
        output=stage.output_summary,
    )
    stage.failure_category = category.value
    await session.commit()

    agent = await _get_agent(session, stage.agent_role)
    if agent:
        agent.status = "idle"
        agent.current_task_id = None
        await session.commit()
        await _safe_broadcast(
            AGENT_STATUS_CHANGED,
            {
                "role": agent.role,
                "status": "idle",
                "current_task_id": None,
                "current_stage": None,
            },
        )

    await _safe_broadcast(
        TASK_STAGE_UPDATE,
        {
            "task_id": task.id,
            "stage_id": stage.id,
            "stage_name": stage.stage_name,
            "status": "failed",
            "error_message": error_message,
        },
    )

    logger.error("Stage %s failed: %s", stage.stage_name, error_message)


async def _get_agent(session: AsyncSession, role: str) -> AgentModel | None:
    result = await session.execute(select(AgentModel).where(AgentModel.role == role))
    return result.scalar_one_or_none()


async def execute_stage_sandboxed(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    prior_outputs: List[Dict[str, str]],
    sandbox_info,
    compressed_outputs: Optional[List[Dict[str, str]]] = None,
    project_memory: Optional[str] = None,
    repo_context: Optional[str] = None,
    retry_context: Optional[Dict[str, str]] = None,
    stage_model: Optional[str] = None,
    custom_instruction: Optional[str] = None,
    gate_rejection_context: Optional[Dict[str, str]] = None,
) -> str:
    """Execute a stage inside a sandbox container via HTTP.

    The sandbox container runs a full AgentRunner (LLM client + tools).
    This function builds the prompt, sends it to the container's agent server,
    and processes the response — handling DB updates and broadcasts as normal.
    """
    from app.worker.agents import ROLE_TOOLS, resolve_model_for_role
    from app.worker.prompts import SYSTEM_PROMPTS, StageContext, build_user_prompt
    from app.worker.sandbox import get_sandbox_manager

    now = datetime.now(timezone.utc)

    # 1. Mark stage as running
    stage.status = "running"
    stage.started_at = now
    await session.commit()

    # 2. Update agent status
    agent = await _get_agent(session, stage.agent_role)
    if agent:
        agent.status = "running"
        agent.current_task_id = task.id
        agent.started_at = now
        agent.last_active_at = now
        await session.commit()
        await _safe_broadcast(AGENT_STATUS_CHANGED, {
            "role": agent.role,
            "status": "running",
            "current_task_id": task.id,
            "current_stage": stage.stage_name,
        })

    # 3. Broadcast stage running
    await _safe_broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "running",
    })

    # 4. Build prompt
    start_time = time.monotonic()
    ctx = StageContext(
        task_title=task.title,
        task_description=task.description,
        stage_name=stage.stage_name,
        agent_role=stage.agent_role,
        prior_outputs=prior_outputs,
        compressed_outputs=compressed_outputs,
        project_memory=project_memory,
        repo_context=repo_context,
        retry_context=retry_context,
        custom_instruction=custom_instruction,
        gate_rejection_context=gate_rejection_context,
    )
    user_prompt = build_user_prompt(ctx)
    system_prompt = SYSTEM_PROMPTS.get(stage.agent_role, SYSTEM_PROMPTS["orchestrator"])
    system_prompt += "\n\n你的工作目录是: /workspace\n所有文件操作请在此目录下进行。"

    resolved_model = resolve_model_for_role(stage.agent_role, stage_model)
    allowed_tools = list(ROLE_TOOLS.get(stage.agent_role, set()))

    # Resolve skill directories for the role
    from app.worker.agents import _get_skill_dirs
    skill_dirs = [f"/skills/{d.name}" for d in _get_skill_dirs(stage.agent_role)]

    max_turns_map = {"spec": 20, "coding": 20, "doc": 20, "test": 20}
    max_turns = max_turns_map.get(stage.agent_role, 10)

    # 5. Log the request via shared pipeline contract
    pipeline = get_task_log_pipeline()
    task_id = str(task.id)
    stage_id = str(stage.id)
    chat_correlation = f"chat-{uuid.uuid4().hex}"
    await pipeline.emit_create(
        task_id=task_id,
        stage_id=stage_id,
        stage_name=stage.stage_name,
        agent_role=stage.agent_role,
        event_type="agent_runner_chat_sent",
        event_source="llm",
        status="running",
        correlation_id=chat_correlation,
        request_body={
            "sandbox": sandbox_info.container_name,
            "model": resolved_model,
            "stage": stage.stage_name,
            "agent_role": stage.agent_role,
            "prompt": user_prompt,
        },
        workspace="/workspace",
        execution_mode="sandbox",
        priority="high",
    )

    # 6. Send to sandbox container
    sandbox_mgr = get_sandbox_manager()
    sandbox_result = await sandbox_mgr.execute_stage(
        sandbox_info,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=resolved_model,
        max_turns=max_turns,
        enable_tools=True,
        allowed_tools=allowed_tools,
        skill_dirs=skill_dirs,
        workdir="/workspace",
        timeout=int(settings.WORKER_STAGE_TIMEOUT),
    )

    elapsed = time.monotonic() - start_time

    if sandbox_result.error:
        await pipeline.emit_create(
            task_id=task_id,
            stage_id=stage_id,
            stage_name=stage.stage_name,
            agent_role=stage.agent_role,
            event_type="agent_runner_chat_received",
            event_source="llm",
            status="failed",
            correlation_id=chat_correlation,
            response_body={"error": sandbox_result.error},
            workspace="/workspace",
            execution_mode="sandbox",
            duration_ms=round(elapsed * 1000, 2),
            priority="high",
        )
        raise RuntimeError(f"Sandbox execution failed: {sandbox_result.error}")

    output = sandbox_result.text_content
    total_tokens = sandbox_result.total_tokens

    # Log tool calls from sandbox
    for index, tc in enumerate(sandbox_result.tool_calls):
        tool_name = str(tc.get("tool_name") or "")
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        workspace = str(args.get("cwd") or "/workspace").strip() or "/workspace"
        result_preview = str(tc.get("result_preview") or "")
        raw_status = str(tc.get("status") or "").strip().lower()
        status = raw_status if raw_status in {"success", "failed", "cancelled"} else infer_tool_status(
            result_preview,
        )
        correlation_id = str(tc.get("tool_call_id") or "").strip() or (
            f"{chat_correlation}:tool:{index + 1}"
        )
        await pipeline.emit_create(
            task_id=task_id,
            stage_id=stage_id,
            stage_name=stage.stage_name,
            agent_role=stage.agent_role,
            event_type="tool_call_executed",
            event_source="tool",
            status=status,
            correlation_id=correlation_id,
            command=_summarize_tool_command(tool_name, args),
            command_args={"tool_name": tool_name, **args},
            workspace=workspace,
            execution_mode="sandbox",
            duration_ms=_float_or_none(tc.get("duration_ms")),
            result=result_preview,
            output_summary=result_preview,
            priority="high",
        )

    # Log success response
    await pipeline.emit_create(
        task_id=task_id,
        stage_id=stage_id,
        stage_name=stage.stage_name,
        agent_role=stage.agent_role,
        event_type="agent_runner_chat_received",
        event_source="llm",
        status="success",
        correlation_id=chat_correlation,
        response_body={
            "content": output[:2000] if output else "",
            "total_tokens": total_tokens,
            "tool_calls_count": len(sandbox_result.tool_calls),
        },
        workspace="/workspace",
        execution_mode="sandbox",
        duration_ms=round(elapsed * 1000, 2),
        priority="high",
    )

    # 7. Update stage as completed
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = round(elapsed, 2)
    stage.tokens_used = total_tokens
    stage.output_summary = output
    await session.commit()

    # 8. Update task total tokens and cost
    task.total_tokens += total_tokens
    cost = total_tokens * settings.CB_TOKEN_PRICE_PER_1K / 1000
    task.total_cost_rmb += cost
    await session.commit()

    # 9. Broadcast stage completed
    await _safe_broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "completed",
        "duration_seconds": stage.duration_seconds,
        "tokens_used": total_tokens,
    })

    # 10. Reset agent to idle
    if agent:
        agent.status = "idle"
        agent.current_task_id = None
        agent.last_active_at = datetime.now(timezone.utc)
        await session.commit()
        await _safe_broadcast(AGENT_STATUS_CHANGED, {
            "role": agent.role,
            "status": "idle",
            "current_task_id": None,
            "current_stage": None,
        })

    logger.info(
        "Stage %s completed via sandbox: %.1fs, %d tokens, %d tool calls",
        stage.stage_name, elapsed, total_tokens, len(sandbox_result.tool_calls),
    )
    return output
