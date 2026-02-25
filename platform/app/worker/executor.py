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

from app.models.agent import AgentModel
from app.models.task import TaskModel, TaskStageModel
from app.services.task_log_service import TaskLogService
from app.websocket.events import AGENT_STATUS_CHANGED, TASK_STAGE_LOG, TASK_STAGE_UPDATE
from app.websocket.manager import ws_manager
from app.config import settings
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
    agent: AgentModel | None, stage_model: Optional[str]
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
    if (
        "temperature" in sig.parameters
        and runtime_overrides.get("temperature") is not None
    ):
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
    """Execute a single stage: call AgentRunner and update DB/broadcast.

    Args:
        retry_context: Failure info from previous attempt for smart retry.
        stage_model: Per-stage LLM model override.
        workdir_override: If set, override the agent's default cwd (e.g. git worktree path).

    Returns the agent output text.
    """
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

    # 4. Build prompt and call AgentRunner
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

    log_service = TaskLogService(session)
    stage_logs: list[dict[str, Any]] = []
    tool_call_starts: dict[str, float] = {}

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

    def _append_stage_log(
        *,
        event_type: str,
        event_source: str,
        status: str,
        request_body: Optional[dict[str, Any]] = None,
        response_body: Optional[dict[str, Any]] = None,
        command: Optional[str] = None,
        command_args: Optional[dict[str, Any]] = None,
        workspace: Optional[str] = None,
        duration_ms: Optional[float] = None,
        result: Optional[str] = None,
        missing_fields: Optional[list[str]] = None,
        created_at: Optional[datetime] = None,
    ) -> None:
        ts = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
        stage_logs.append(
            {
                "task_id": str(task.id),
                "stage_id": str(stage.id),
                "stage_name": stage.stage_name,
                "agent_role": stage.agent_role,
                "event_type": event_type,
                "event_source": event_source,
                "status": status,
                "request_body": request_body,
                "response_body": response_body,
                "command": command,
                "command_args": command_args,
                "workspace": workspace,
                "duration_ms": duration_ms,
                "result": result,
                "missing_fields": missing_fields or [],
                # Record event occurrence time, instead of stage-end flush time.
                "created_at": ts,
            }
        )

        # Broadcast real-time stage log event (fire-and-forget)
        if event_type in ("tool_call_executed", "llm_response_received"):
            payload = {
                "task_id": str(task.id),
                "stage_id": str(stage.id),
                "stage_name": stage.stage_name,
                "event_type": event_type,
                "event_source": event_source,
                "status": status,
                "command": command,
                "duration_ms": duration_ms,
                "timestamp": ts.isoformat(),
            }
            # Truncate result for WS payload (avoid huge messages)
            if result:
                payload["result_preview"] = result[:500]
            if response_body and response_body.get("content"):
                payload["result_preview"] = str(response_body["content"])[:500]
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(_safe_broadcast(TASK_STAGE_LOG, payload))
            except RuntimeError:
                pass

    runtime_overrides = _build_runtime_overrides(agent, stage_model)

    # Create runner with runtime config routing
    runner = get_agent(
        stage.agent_role,
        str(task.id),
        model=runtime_overrides["model"],
        max_turns=runtime_overrides["max_turns"],
        extra_skill_dirs=runtime_overrides["extra_skill_dirs"],
        system_prompt_append=runtime_overrides["system_prompt_append"],
    )
    # Override working directory if worktree is provided (e.g. git worktree)
    if workdir_override and runner.default_cwd != workdir_override:
        runner.default_cwd = workdir_override
    runner.reset_usage()

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

        def _on_before_tool_call(event: Any) -> None:
            tool_call_id = str(getattr(event, "tool_call_id", ""))
            if tool_call_id:
                tool_call_starts[tool_call_id] = time.monotonic()

        def _on_after_tool_result(event: Any) -> None:
            tool_call_id = str(getattr(event, "tool_call_id", ""))
            tool_name = str(getattr(event, "tool_name", ""))
            args = getattr(event, "args", {}) or {}
            if not isinstance(args, dict):
                args = {}
            output = str(getattr(event, "result", ""))
            started = tool_call_starts.pop(tool_call_id, None)
            duration_ms = (
                round((time.monotonic() - started) * 1000, 2)
                if started is not None
                else None
            )
            command = _summarize_tool_command(tool_name, args)
            workspace = args.get("cwd") or fallback_workspace
            status = infer_tool_status(output)
            missing_fields: list[str] = []
            if not workspace:
                missing_fields.append("workspace")

            _append_stage_log(
                event_type="tool_call_executed",
                event_source="tool",
                status=status,
                command=command,
                command_args={"tool_name": tool_name, **args},
                workspace=workspace,
                duration_ms=duration_ms,
                result=output,
                missing_fields=missing_fields,
            )

        current_runner.events.on("before_tool_call", _on_before_tool_call, source=handler_source)
        current_runner.events.on("after_tool_result", _on_after_tool_result, source=handler_source)

    _register_runner_events(runner)

    # Tool-call error patterns (e.g. MiniMax model returns invalid tool JSON)
    _TOOL_CALL_ERROR_PATTERNS = (
        "invalid function arguments",
        "invalid_request_error",
        "tool_use_failed",
        "function_call",
    )

    def _is_tool_call_error(err: Exception) -> bool:
        """Check if the error is caused by the LLM generating invalid tool calls."""
        msg = str(err).lower()
        return any(p in msg for p in _TOOL_CALL_ERROR_PATTERNS)

    # Retry loop with exponential backoff + per-call timeout
    last_error: Optional[Exception] = None
    used_text_only_fallback = False
    response: Any | None = None
    try:
        for attempt in range(settings.WORKER_STAGE_MAX_RETRIES + 1):
            llm_started = time.monotonic()
            _append_stage_log(
                event_type="llm_request_sent",
                event_source="llm",
                status="sent",
                request_body={
                    "attempt": attempt + 1,
                    "timeout_seconds": settings.WORKER_STAGE_TIMEOUT,
                    "model": getattr(getattr(runner, "config", None), "model", None),
                    "stage": stage.stage_name,
                    "agent_role": stage.agent_role,
                    "prompt": user_prompt,
                    "temperature": runtime_overrides.get("temperature"),
                    "max_tokens": runtime_overrides.get("max_tokens"),
                },
            )
            try:
                chat_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
                response = await asyncio.wait_for(
                    runner.chat(user_prompt, reset=True, **chat_kwargs),
                    timeout=settings.WORKER_STAGE_TIMEOUT,
                )
                _append_stage_log(
                    event_type="llm_response_received",
                    event_source="llm",
                    status="success",
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                    response_body={
                        "attempt": attempt + 1,
                        "content": response.text_content,
                    },
                )
                break
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(
                    f"Stage {stage.stage_name} LLM call timed out "
                    f"after {settings.WORKER_STAGE_TIMEOUT}s"
                )
                _append_stage_log(
                    event_type="llm_response_received",
                    event_source="llm",
                    status="failed",
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                    response_body={"attempt": attempt + 1, "error": str(last_error)},
                )
                logger.warning(
                    "Stage %s LLM call timed out (attempt %d/%d, timeout=%.0fs)",
                    stage.stage_name, attempt + 1, settings.WORKER_STAGE_MAX_RETRIES + 1,
                    settings.WORKER_STAGE_TIMEOUT,
                )
                if attempt >= settings.WORKER_STAGE_MAX_RETRIES:
                    await log_service.append_logs(stage_logs)
                    await session.commit()
                    raise last_error
                delay = settings.WORKER_STAGE_RETRY_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                _append_stage_log(
                    event_type="llm_response_received",
                    event_source="llm",
                    status="failed",
                    duration_ms=round((time.monotonic() - llm_started) * 1000, 2),
                    response_body={"attempt": attempt + 1, "error": str(e)},
                )
                # Detect tool-calling errors and fall back to text-only mode
                if _is_tool_call_error(e) and not used_text_only_fallback:
                    logger.warning(
                        "Stage %s tool-call error detected, falling back to text-only mode: %s",
                        stage.stage_name, e,
                    )
                    runner = get_agent_text_only(
                        stage.agent_role,
                        str(task.id),
                        model=runtime_overrides["model"],
                        max_turns=runtime_overrides["max_turns"],
                        extra_skill_dirs=runtime_overrides["extra_skill_dirs"],
                        system_prompt_append=runtime_overrides["system_prompt_append"],
                    )
                    runner.reset_usage()
                    _register_runner_events(runner)
                    used_text_only_fallback = True
                    # Don't count this as a retry attempt — continue the loop
                    continue
                if attempt < settings.WORKER_STAGE_MAX_RETRIES:
                    delay = settings.WORKER_STAGE_RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        "Stage %s LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                        stage.stage_name, attempt + 1, settings.WORKER_STAGE_MAX_RETRIES + 1,
                        delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Stage %s LLM call failed after %d attempts",
                        stage.stage_name, settings.WORKER_STAGE_MAX_RETRIES + 1,
                    )
                    await log_service.append_logs(stage_logs)
                    await session.commit()
                    raise last_error
    finally:
        for instrumented in instrumented_runners:
            try:
                instrumented.events.off_by_source(handler_source)
            except Exception:
                logger.warning("Failed to detach stage log handlers", exc_info=True)

    if response is None:
        raise RuntimeError(f"Stage {stage.stage_name} returned no response")

    elapsed = time.monotonic() - start_time

    output = response.text_content
    total_tokens = runner.cumulative_usage.total_tokens

    # Detect truncated output from max turns limit and attempt continuation
    _MAX_CONTINUATIONS = 3
    _TRUNCATION_SENTINEL = "Max turns reached"
    continuations = 0
    while _TRUNCATION_SENTINEL in (output or "") and continuations < _MAX_CONTINUATIONS:
        continuations += 1
        continuation_started = time.monotonic()
        _append_stage_log(
            event_type="llm_request_sent",
            event_source="llm",
            status="sent",
            request_body={
                "continuation": continuations,
                "timeout_seconds": settings.WORKER_STAGE_TIMEOUT,
                "stage": stage.stage_name,
                "agent_role": stage.agent_role,
                "prompt": "请继续完成上面的输出，从你停下的地方继续。",
            },
        )
        logger.warning(
            "Stage %s hit max turns (continuation %d/%d), sending continue prompt",
            stage.stage_name, continuations, _MAX_CONTINUATIONS,
        )
        try:
            continuation_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
            cont_response = await asyncio.wait_for(
                runner.chat(
                    "请继续完成上面的输出，从你停下的地方继续。",
                    reset=False,
                    **continuation_kwargs,
                ),
                timeout=settings.WORKER_STAGE_TIMEOUT,
            )
            cont_text = cont_response.text_content or ""
            _append_stage_log(
                event_type="llm_response_received",
                event_source="llm",
                status="success",
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
                response_body={"continuation": continuations, "content": cont_text},
            )
            # Replace the sentinel with continuation output
            output = output.replace(
                f"[{_TRUNCATION_SENTINEL}. Please continue the conversation.]", "",
            ).strip()
            output = f"{output}\n\n{cont_text}".strip()
            total_tokens = runner.cumulative_usage.total_tokens
        except Exception as e:
            _append_stage_log(
                event_type="llm_response_received",
                event_source="llm",
                status="failed",
                duration_ms=round((time.monotonic() - continuation_started) * 1000, 2),
                response_body={"continuation": continuations, "error": str(e)},
            )
            logger.warning(
                "Continuation %d failed for stage %s: %s",
                continuations, stage.stage_name, e,
            )
            break

    # If output still contains the sentinel after all continuations, log a warning
    if _TRUNCATION_SENTINEL in (output or ""):
        logger.warning(
            "Stage %s output still truncated after %d continuations",
            stage.stage_name, _MAX_CONTINUATIONS,
        )

    # 5. Update stage as completed
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = round(elapsed, 2)
    stage.tokens_used = total_tokens
    stage.output_summary = output
    await log_service.append_logs(stage_logs)
    await session.commit()

    # 6. Update task total tokens and cost
    task.total_tokens += total_tokens
    cost = total_tokens * settings.CB_TOKEN_PRICE_PER_1K / 1000
    task.total_cost_rmb += cost
    await session.commit()

    # 7. Broadcast stage completed
    await _safe_broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "completed",
        "duration_seconds": stage.duration_seconds,
        "tokens_used": total_tokens,
    })

    # 8. Reset agent to idle
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
        "Stage %s completed: %.1fs, %d tokens",
        stage.stage_name,
        elapsed,
        total_tokens,
    )
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

    # Reset agent
    agent = await _get_agent(session, stage.agent_role)
    if agent:
        agent.status = "idle"
        agent.current_task_id = None
        await session.commit()
        await _safe_broadcast(AGENT_STATUS_CHANGED, {
            "role": agent.role,
            "status": "idle",
            "current_task_id": None,
            "current_stage": None,
        })

    # Broadcast failure
    await _safe_broadcast(TASK_STAGE_UPDATE, {
        "task_id": task.id,
        "stage_id": stage.id,
        "stage_name": stage.stage_name,
        "status": "failed",
        "error_message": error_message,
    })

    logger.error("Stage %s failed: %s", stage.stage_name, error_message)


async def _get_agent(session: AsyncSession, role: str) -> AgentModel | None:
    result = await session.execute(
        select(AgentModel).where(AgentModel.role == role)
    )
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
