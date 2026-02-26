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
from app.services.task_log_service import TaskLogService
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
    active_chat_correlation_id: dict[str, Optional[str]] = {"value": None}
    tool_runs: dict[str, dict[str, Any]] = {}
    chat_runs: dict[str, dict[str, Any]] = {}
    turn_runs: dict[str, dict[str, Any]] = {}

    async def _emit_system_event(
        event_type: str,
        *,
        status: str,
        response_body: Optional[dict[str, Any]] = None,
        result: Optional[str] = None,
        priority: str = "normal",
    ) -> None:
        await pipeline.emit_create(
            task_id=task_id,
            stage_id=stage_id,
            stage_name=stage.stage_name,
            agent_role=stage.agent_role,
            event_type=event_type,
            event_source="system",
            status=status,
            response_body=response_body,
            result=result,
            priority=priority,  # type: ignore[arg-type]
        )

    async def _emit_chat_sent(
        *,
        prompt: str,
        metadata: dict[str, Any],
    ) -> str:
        correlation_id = f"chat-{uuid.uuid4().hex}"
        active_chat_correlation_id["value"] = correlation_id
        log_id = await pipeline.emit_create(
            task_id=task_id,
            stage_id=stage_id,
            stage_name=stage.stage_name,
            agent_role=stage.agent_role,
            event_type="agent_runner_chat_sent",
            event_source="llm",
            status="running",
            correlation_id=correlation_id,
            request_body={
                "prompt": prompt,
                "model": getattr(getattr(runner, "config", None), "model", None),
                "stage": stage.stage_name,
                "agent_role": stage.agent_role,
                "temperature": runtime_overrides.get("temperature"),
                "max_tokens": runtime_overrides.get("max_tokens"),
                **metadata,
            },
            priority="high",
        )
        chat_runs[correlation_id] = {
            "log_id": log_id,
            "started": time.monotonic(),
        }
        return correlation_id

    async def _emit_chat_received(
        correlation_id: str,
        *,
        status: str,
        response_body: dict[str, Any],
        duration_ms: float,
    ) -> None:
        run_info = chat_runs.get(correlation_id)
        effective_duration = duration_ms
        if run_info is not None:
            effective_duration = round((time.monotonic() - run_info["started"]) * 1000, 2)
            await pipeline.emit_update(
                log_id=run_info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": effective_duration,
                },
                priority="high",
            )
            chat_runs.pop(correlation_id, None)
        await pipeline.emit_create(
            task_id=task_id,
            stage_id=stage_id,
            stage_name=stage.stage_name,
            agent_role=stage.agent_role,
            event_type="agent_runner_chat_received",
            event_source="llm",
            status=status,
            correlation_id=correlation_id,
            response_body=response_body,
            duration_ms=effective_duration,
            priority="high",
        )
        if active_chat_correlation_id["value"] == correlation_id:
            active_chat_correlation_id["value"] = None

    handler_source = f"stage-log:{task.id}:{stage.id}:{uuid.uuid4().hex}"
    instrumented_runners: list[Any] = []
    instrumented_runner_ids: set[int] = set()

    def _register_runner_events(current_runner: Any) -> None:
        rid = id(current_runner)
        if rid in instrumented_runner_ids:
            return
        instrumented_runner_ids.add(rid)
        instrumented_runners.append(current_runner)
        fallback_workspace = getattr(current_runner, "default_cwd", None)

        async def _on_turn_start(event: Any) -> None:
            chat_correlation = active_chat_correlation_id.get("value")
            if not chat_correlation:
                return
            turn = int(getattr(event, "turn", 0))
            correlation = f"{chat_correlation}:turn:{turn}"
            log_id = await pipeline.emit_create(
                task_id=task_id,
                stage_id=stage_id,
                stage_name=stage.stage_name,
                agent_role=stage.agent_role,
                event_type="llm_turn_sent",
                event_source="llm",
                status="running",
                correlation_id=correlation,
                request_body={
                    "turn": turn,
                    "message_count": int(getattr(event, "message_count", 0)),
                },
            )
            turn_runs[correlation] = {
                "log_id": log_id,
                "started": time.monotonic(),
            }

        async def _on_turn_end(event: Any) -> None:
            chat_correlation = active_chat_correlation_id.get("value")
            if not chat_correlation:
                return
            turn = int(getattr(event, "turn", 0))
            correlation = f"{chat_correlation}:turn:{turn}"
            duration_ms: Optional[float] = None
            run_info = turn_runs.get(correlation)
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                await pipeline.emit_update(
                    log_id=run_info["log_id"],
                    updates={
                        "status": "success",
                        "duration_ms": duration_ms,
                    },
                    priority="high",
                )
                turn_runs.pop(correlation, None)
            await pipeline.emit_create(
                task_id=task_id,
                stage_id=stage_id,
                stage_name=stage.stage_name,
                agent_role=stage.agent_role,
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

            log_id = await pipeline.emit_create(
                task_id=task_id,
                stage_id=stage_id,
                stage_name=stage.stage_name,
                agent_role=stage.agent_role,
                event_type="tool_call_executed",
                event_source="tool",
                status="running",
                correlation_id=tool_call_id,
                command=_summarize_tool_command(tool_name, args),
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                missing_fields=missing_fields,
                priority="high",
            )
            tool_runs[tool_call_id] = {
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
            run_info = tool_runs.get(tool_call_id)
            if run_info is not None:
                summary, truncated = _append_output_summary(run_info["summary"], chunk)
                run_info["summary"] = summary
                run_info["truncated"] = run_info["truncated"] or truncated
                await _safe_broadcast(
                    TASK_LOG_STREAM_UPDATE,
                    {
                        "task_id": task_id,
                        "stage_id": stage_id,
                        "stage_name": stage.stage_name,
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

            run_info = tool_runs.get(tool_call_id)
            duration_ms: Optional[float] = None
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                output_summary = run_info["summary"] or output
                await pipeline.emit_update(
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
                        "task_id": task_id,
                        "stage_id": stage_id,
                        "stage_name": stage.stage_name,
                        "log_id": run_info["log_id"],
                        "tool_call_id": tool_call_id,
                        "chunk": "",
                        "finished": True,
                        "status": status,
                    },
                )
                tool_runs.pop(tool_call_id, None)
                return

            workspace = args.get("cwd") or fallback_workspace
            missing_fields: list[str] = []
            if not workspace:
                missing_fields.append("workspace")
            await pipeline.emit_create(
                task_id=task_id,
                stage_id=stage_id,
                stage_name=stage.stage_name,
                agent_role=stage.agent_role,
                event_type="tool_call_executed",
                event_source="tool",
                status=status,
                correlation_id=tool_call_id,
                command=_summarize_tool_command(tool_name, args),
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                duration_ms=duration_ms,
                result=output,
                output_summary=output,
                missing_fields=missing_fields,
                priority="high",
            )

        current_runner.events.on("turn_start", _on_turn_start, source=handler_source)
        current_runner.events.on("turn_end", _on_turn_end, source=handler_source)
        current_runner.events.on("before_tool_call", _on_before_tool_call, source=handler_source)
        current_runner.events.on(
            "tool_execution_update",
            _on_tool_execution_update,
            source=handler_source,
        )
        current_runner.events.on("after_tool_result", _on_after_tool_result, source=handler_source)

    _register_runner_events(runner)

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

    async def _finalize_unfinished_turns(status: str, reason: str) -> None:
        for correlation, info in list(turn_runs.items()):
            await pipeline.emit_update(
                log_id=info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": round((time.monotonic() - info["started"]) * 1000, 2),
                    "result": reason,
                },
                priority="high",
            )
            turn_runs.pop(correlation, None)

    async def _finalize_unfinished_chats(status: str, reason: str) -> None:
        active_chat_correlation_id["value"] = None
        for correlation, info in list(chat_runs.items()):
            await pipeline.emit_update(
                log_id=info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": round((time.monotonic() - info["started"]) * 1000, 2),
                    "result": reason,
                },
                priority="high",
            )
            chat_runs.pop(correlation, None)

    async def _finalize_unfinished_tools(status: str, reason: str) -> None:
        for tool_call_id, info in list(tool_runs.items()):
            await pipeline.emit_update(
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
                    "task_id": task_id,
                    "stage_id": stage_id,
                    "stage_name": stage.stage_name,
                    "log_id": info["log_id"],
                    "tool_call_id": tool_call_id,
                    "chunk": "",
                    "finished": True,
                    "status": status,
                },
            )
            tool_runs.pop(tool_call_id, None)

    last_error: BaseException | None = None
    used_text_only_fallback = False
    response: Any | None = None

    try:
        for attempt in range(settings.WORKER_STAGE_MAX_RETRIES + 1):
            llm_started = time.monotonic()
            chat_correlation = await _emit_chat_sent(
                prompt=user_prompt,
                metadata={
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
                await _emit_chat_received(
                    chat_correlation,
                    status="success",
                    response_body={"attempt": attempt + 1, "content": response.text_content},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )
                break
            except asyncio.CancelledError as e:
                last_error = e
                _clear_current_task_cancellation_state()
                await _emit_chat_received(
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
                await _emit_chat_received(
                    chat_correlation,
                    status="failed",
                    response_body={"attempt": attempt + 1, "error": str(last_error)},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )
                if attempt >= settings.WORKER_STAGE_MAX_RETRIES:
                    raise last_error
                delay = settings.WORKER_STAGE_RETRY_DELAY * (2 ** attempt)
                await _emit_system_event(
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
                await _emit_chat_received(
                    chat_correlation,
                    status="failed",
                    response_body={"attempt": attempt + 1, "error": str(e)},
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                )

                if _is_tool_call_error(e) and not used_text_only_fallback:
                    await _emit_system_event(
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
                    _register_runner_events(runner)
                    used_text_only_fallback = True
                    continue

                if attempt < settings.WORKER_STAGE_MAX_RETRIES:
                    delay = settings.WORKER_STAGE_RETRY_DELAY * (2 ** attempt)
                    await _emit_system_event(
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
        for instrumented in instrumented_runners:
            try:
                instrumented.events.off_by_source(handler_source)
            except Exception:
                logger.warning("Failed to detach stage log handlers", exc_info=True)

        if isinstance(last_error, asyncio.CancelledError):
            finalize_status = "cancelled"
            finalize_reason = "execution cancelled before lifecycle closeout"
        elif last_error is not None:
            finalize_status = "failed"
            finalize_reason = f"execution failed before lifecycle closeout: {last_error}"
        else:
            finalize_status = "cancelled"
            finalize_reason = "execution ended before lifecycle closeout"

        await _finalize_unfinished_turns(finalize_status, finalize_reason)
        await _finalize_unfinished_chats(finalize_status, finalize_reason)
        await _finalize_unfinished_tools(finalize_status, finalize_reason)

    if response is None:
        raise RuntimeError(f"Stage {stage.stage_name} returned no response")

    elapsed = time.monotonic() - start_time
    output = response.text_content
    total_tokens = runner.cumulative_usage.total_tokens

    _MAX_CONTINUATIONS = 3
    _TRUNCATION_SENTINEL = "Max turns reached"
    continuations = 0

    while _TRUNCATION_SENTINEL in (output or "") and continuations < _MAX_CONTINUATIONS:
        continuations += 1
        continuation_started = time.monotonic()
        prompt = "请继续完成上面的输出，从你停下的地方继续。"
        chat_correlation = await _emit_chat_sent(
            prompt=prompt,
            metadata={
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
            await _emit_chat_received(
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
            total_tokens = runner.cumulative_usage.total_tokens
        except asyncio.CancelledError as e:
            last_error = e
            _clear_current_task_cancellation_state()
            await _emit_chat_received(
                chat_correlation,
                status="cancelled",
                response_body={"continuation": continuations, "error": "cancelled"},
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
            )
            raise
        except Exception as e:
            await _emit_chat_received(
                chat_correlation,
                status="failed",
                response_body={"continuation": continuations, "error": str(e)},
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
            )
            break

    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = round(elapsed, 2)
    stage.tokens_used = total_tokens
    stage.output_summary = output
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
    return output


async def mark_stage_failed(
    session: AsyncSession,
    task: TaskModel,
    stage: TaskStageModel,
    error_message: str,
) -> None:
    """Mark a stage as failed and reset the agent."""
    stage.status = "failed"
    stage.error_message = error_message
    stage.completed_at = datetime.now(timezone.utc)
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

    # 5. Log the request
    log_service = TaskLogService(session)
    stage_logs: list[dict[str, Any]] = []

    stage_logs.append({
        "task_id": str(task.id),
        "stage_id": str(stage.id),
        "stage_name": stage.stage_name,
        "agent_role": stage.agent_role,
        "event_type": "llm_request_sent",
        "event_source": "sandbox",
        "status": "sent",
        "request_body": {
            "sandbox": sandbox_info.container_name,
            "model": resolved_model,
            "stage": stage.stage_name,
            "agent_role": stage.agent_role,
            "prompt": user_prompt,
        },
        "response_body": None,
        "command": None,
        "command_args": None,
        "workspace": "/workspace",
        "duration_ms": None,
        "result": None,
        "missing_fields": [],
    })

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
        stage_logs.append({
            "task_id": str(task.id),
            "stage_id": str(stage.id),
            "stage_name": stage.stage_name,
            "agent_role": stage.agent_role,
            "event_type": "llm_response_received",
            "event_source": "sandbox",
            "status": "failed",
            "request_body": None,
            "response_body": {"error": sandbox_result.error},
            "command": None,
            "command_args": None,
            "workspace": "/workspace",
            "duration_ms": round(elapsed * 1000, 2),
            "result": None,
            "missing_fields": [],
        })
        await log_service.append_logs(stage_logs)
        await session.commit()
        raise RuntimeError(f"Sandbox execution failed: {sandbox_result.error}")

    output = sandbox_result.text_content
    total_tokens = sandbox_result.total_tokens

    # Log tool calls from sandbox
    for tc in sandbox_result.tool_calls:
        stage_logs.append({
            "task_id": str(task.id),
            "stage_id": str(stage.id),
            "stage_name": stage.stage_name,
            "agent_role": stage.agent_role,
            "event_type": "tool_call_executed",
            "event_source": "sandbox_tool",
            "status": tc.get("status", "success"),
            "request_body": None,
            "response_body": None,
            "command": tc.get("tool_name", ""),
            "command_args": tc.get("args", {}),
            "workspace": tc.get("args", {}).get("cwd", "/workspace"),
            "duration_ms": tc.get("duration_ms"),
            "result": tc.get("result_preview", ""),
            "missing_fields": [],
        })

    # Log success response
    stage_logs.append({
        "task_id": str(task.id),
        "stage_id": str(stage.id),
        "stage_name": stage.stage_name,
        "agent_role": stage.agent_role,
        "event_type": "llm_response_received",
        "event_source": "sandbox",
        "status": "success",
        "request_body": None,
        "response_body": {
            "content": output[:2000] if output else "",
            "total_tokens": total_tokens,
            "tool_calls_count": len(sandbox_result.tool_calls),
        },
        "command": None,
        "command_args": None,
        "workspace": "/workspace",
        "duration_ms": round(elapsed * 1000, 2),
        "result": None,
        "missing_fields": [],
    })

    # 7. Update stage as completed
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = round(elapsed, 2)
    stage.tokens_used = total_tokens
    stage.output_summary = output
    await log_service.append_logs(stage_logs)
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
