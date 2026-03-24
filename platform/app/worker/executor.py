"""Single-stage executor: build prompt -> AgentRunner chat -> update DB -> broadcast events."""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
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
from app.worker.agents import get_agent, get_agent_text_only
from app.worker.prompts import StageContext, build_user_prompt
from app.worker.stage_event_tracker import (
    StageEventTracker,
    _append_output_summary,
    _safe_broadcast,
    _summarize_tool_command,
    infer_tool_status,
)

logger = logging.getLogger(__name__)

_WORKDIR_PROMPT_PATTERN = re.compile(
    r"\n\n你的工作目录是: .*\n所有文件操作请在此目录下进行。",
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


_DEFAULT_STAGE_MAX_TURNS: dict[str, int] = {
    "spec": 10,
    "coding": 6,
    "doc": 10,
    "test": 6,
    "dispatch issue": 5,
    "des encrypt": 15,
}

_STAGE_MAX_TURN_CAPS: dict[str, int] = {
    "coding": 6,
    "test": 6,
}


def _resolve_stage_max_turns(agent_role: str, override: Optional[int]) -> int:
    default_value = _DEFAULT_STAGE_MAX_TURNS.get(agent_role, 10)
    requested = override if isinstance(override, int) and override > 0 else default_value
    cap = _STAGE_MAX_TURN_CAPS.get(agent_role)
    return min(requested, cap) if cap else requested


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



def _apply_runner_workspace_override(runner: Any, workdir_override: Optional[str]) -> None:
    """Keep runner cwd and system prompt workspace hint aligned with runtime override."""
    if not workdir_override:
        return

    if getattr(runner, "default_cwd", None) != workdir_override:
        runner.default_cwd = workdir_override

    config = getattr(runner, "config", None)
    system_prompt = getattr(config, "system_prompt", None) if config else None
    if not isinstance(system_prompt, str):
        return

    workspace_hint = (
        f"\n\n你的工作目录是: {workdir_override}\n所有文件操作请在此目录下进行。"
    )
    if _WORKDIR_PROMPT_PATTERN.search(system_prompt):
        system_prompt = _WORKDIR_PROMPT_PATTERN.sub(workspace_hint, system_prompt, count=1)
    else:
        system_prompt = f"{system_prompt}{workspace_hint}"
    config.system_prompt = system_prompt



def _clip_text(value: str, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 15)].rstrip() + "...[truncated]"


def _is_signoff_stage(stage_name: str) -> bool:
    lowered = (stage_name or "").lower()
    return lowered == "signoff" or "signoff" in lowered or "签收" in (stage_name or "")


def _is_text_only_stage(stage_name: str) -> bool:
    return False


def _output_summary_limit(stage_name: str) -> int:
    # Cap stage output stored in DB to limit downstream prior-context injection.
    normalized = (stage_name or "").strip().lower()
    if normalized == "parse":
        return 600
    if normalized in {"code", "coding", "test"}:
        return 1200
    if _is_signoff_stage(normalized):
        return 1500
    if normalized in {"spec", "approve", "review", "doc"}:
        return 1800
    return 1500


def _format_tool_digest(tool_items: list[dict[str, str]], limit: int = 6) -> str:
    if not tool_items:
        return ""
    lines: list[str] = []
    for item in tool_items[-limit:]:
        status = str(item.get("status") or "success").upper()
        command = _clip_text(str(item.get("command") or "tool"), 140)
        preview = _clip_text(str(item.get("result_preview") or ""), 260)
        line = f"- [{status}] {command}"
        if preview:
            line += f"\n  结果: {preview}"
        lines.append(line)
    return "\n".join(lines)


def _resolve_stage_output_summary(
    stage_name: str,
    output: str,
    tool_items: list[dict[str, str]],
) -> str:
    resolved = (output or "").strip()
    if not resolved:
        resolved = _format_tool_digest(tool_items)

    if _is_signoff_stage(stage_name):
        digest = _format_tool_digest(tool_items)
        if digest:
            if resolved:
                resolved = f"{resolved}\n\n## Signoff Summary\n{digest}"
            else:
                resolved = f"## Signoff Summary\n{digest}"

    return _clip_text(resolved, _output_summary_limit(stage_name))


def _stage_goal_summary(stage_name: str | None) -> str:
    normalized = (stage_name or "").strip().lower()
    if normalized in {"code", "coding"}:
        return "直接完成最小必要代码修改，并提供最小验证结果。"
    if normalized == "test":
        return "直接完成最小、最相关的验证，并明确成功或阻塞结论。"
    if normalized == "des encrypt":
        return (
            "继续完成加密改造的剩余工作。"
            "如果代码文件已创建但 Entity/Mapper 还未修改，请立即修改。"
            "如果代码改造已完成，请 git add/commit/push，然后调用 github_issue_feedback skill 回帖。"
        )
    return "完成当前阶段的最终结果。"


def _prefer_restart_continuations(stage_name: str | None) -> bool:
    normalized = (stage_name or "").strip().lower()
    return normalized in {"code", "coding", "test", "des encrypt"}


# ---------------------------------------------------------------------------
# Module-level helpers extracted from execute_stage
# ---------------------------------------------------------------------------

_TOOL_CALL_ERROR_PATTERNS = (
    "invalid function arguments",
    "invalid_request_error",
    "tool_use_failed",
    "function_call",
    "thought_signature",
    "function call is missing a thought_signature",
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


_RESTART_OUTPUT_CHARS = 700


# ---------------------------------------------------------------------------
# Extracted helpers: continuations and stage success
# ---------------------------------------------------------------------------

def _build_continuation_prompt(stage_name: str | None) -> str:
    normalized = (stage_name or "").strip().lower()
    if normalized == "code":
        return (
            "请停止继续广泛探索。基于已知信息直接修改代码；"
            "如果仍缺信息，只允许再查看 1 个最关键文件，然后必须完成修改并给出最小验证结果。"
        )
    if normalized == "test":
        return (
            "请停止扩展测试范围。只做最小、最相关的验证；"
            "如果验证命令失败，必须直接给出失败命令、关键报错和唯一阻塞点，"
            "不要再用代码阅读代替测试结论。"
        )
    if normalized == "des encrypt":
        return (
            "请继续完成加密改造。查看上方「已创建/修改的文件」列表，不要重复创建已有文件。"
            "如果 Entity/Mapper 还未修改，立即修改；如果代码改造已完成，执行 git add/commit/push。"
            "Push 完成后调用 github_issue_feedback skill 回帖。"
        )
    return "请继续完成上面的输出，从你停下的地方继续。"


def _build_forced_convergence_prompt(stage_name: str | None) -> str:
    normalized = (stage_name or "").strip().lower()
    if normalized == "code":
        return (
            "你已经在当前阶段花了过多轮次进行探索。现在禁止继续浏览仓库。"
            "请直接做最小代码修改，并只执行最小必要验证。"
            "如果仍然无法完成，请只输出唯一阻塞点和证据。"
        )
    if normalized == "test":
        return (
            "你已经在当前阶段花了过多轮次进行探索。现在禁止继续扩展测试范围。"
            "请直接执行最小、最相关的验证。"
            "如果验证命令失败，必须明确给出失败命令、关键报错和唯一阻塞点；"
            "不要仅凭代码阅读判断测试通过。"
        )
    if normalized == "des encrypt":
        return (
            "轮次即将用完。请立即完成剩余工作："
            "如果代码改造已完成，直接 git add/commit/push 并调用 github_issue_feedback skill 回帖。"
            "如果代码改造未完成，只完成最关键的修改，然后 commit/push。"
        )
    return "请立即收敛到当前阶段的最终结果，不要继续扩展。"


def _build_stage_restart_prompt(
    restart_context: dict[str, Any] | None,
    tracker: StageEventTracker,
    output: str,
    *,
    reason: str,
) -> str:
    context = restart_context or {}
    title = str(context.get("task_title") or "").strip()
    description = str(context.get("task_description") or "").strip()
    stage_name = str(context.get("stage_name") or tracker.stage_name).strip()
    preflight_summary = str(context.get("preflight_summary") or "").strip()
    partial_output = _clip_text((output or "").replace("[Max turns reached. Please continue the conversation.]", "").strip(), _RESTART_OUTPUT_CHARS)
    completed_runs = tracker.get_completed_tool_runs()
    tool_digest = _format_tool_digest(completed_runs, limit=2)

    # Collect all successfully written files for restart context
    written_files: list[str] = []
    for item in completed_runs:
        status = str(item.get("status") or "").lower()
        command = str(item.get("command") or "")
        if status == "success" and command.startswith("write "):
            fpath = command[len("write "):].strip()
            written_files.append(fpath)

    action_prompt = (
        _build_forced_convergence_prompt(stage_name)
        if reason == "forced_convergence"
        else _build_continuation_prompt(stage_name)
    )

    parts: list[str] = []
    if title:
        parts.append(f"## 任务\n**{title}**")
        if description:
            parts.append(description)
    parts.append(f"\n## 当前阶段\n{stage_name}")
    parts.append(_stage_goal_summary(stage_name))
    if preflight_summary:
        parts.append(f"\n## 阶段预扫摘要\n{preflight_summary}")
    if written_files:
        parts.append("\n## 已创建/修改的文件\n" + "\n".join(f"- {f}" for f in written_files))
    if partial_output:
        parts.append(f"\n## 当前阶段已有部分输出\n{partial_output}")
    if tool_digest:
        parts.append(f"\n## 最近关键工具结果\n{tool_digest}")
    parts.append("\n## 下一步要求")
    parts.append(action_prompt)
    parts.append("不要重新展开整段历史；只基于上面的当前状态继续完成必要工作。")
    return "\n".join(parts).strip()


async def _run_stage_restart(
    runner: Any,
    output: str,
    runtime_overrides: dict[str, Any],
    tracker: StageEventTracker,
    *,
    reason: str,
    restart_index: int,
    restart_context: dict[str, Any] | None = None,
) -> str:
    prompt = _build_stage_restart_prompt(restart_context, tracker, output, reason=reason)
    chat_started = time.monotonic()
    chat_correlation = await tracker.emit_chat_sent(
        request_body={
            "prompt": prompt,
            "model": getattr(getattr(runner, "config", None), "model", None),
            "stage": tracker.stage_name,
            "agent_role": tracker.agent_role,
            "temperature": runtime_overrides.get("temperature"),
            "max_tokens": runtime_overrides.get("max_tokens"),
            "restart": restart_index,
            "restart_reason": reason,
            "forced_convergence": reason == "forced_convergence",
            "reset": True,
            "timeout_seconds": settings.WORKER_STAGE_TIMEOUT,
        },
    )
    try:
        restart_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
        response = await asyncio.wait_for(
            runner.chat(prompt, reset=True, **restart_kwargs),
            timeout=settings.WORKER_STAGE_TIMEOUT,
        )
        restart_text = response.text_content or ""
        await tracker.emit_chat_received(
            chat_correlation,
            status="success",
            response_body={
                "restart": restart_index,
                "restart_reason": reason,
                "forced_convergence": reason == "forced_convergence",
                "content": restart_text,
            },
            duration_ms=round((time.monotonic() - chat_started) * 1000, 2),
        )
        cleaned = (output or "").replace("[Max turns reached. Please continue the conversation.]", "").strip()
        return f"{cleaned}\n\n{restart_text}".strip() if cleaned else restart_text
    except asyncio.CancelledError:
        _clear_current_task_cancellation_state()
        await tracker.emit_chat_received(
            chat_correlation,
            status="cancelled",
            response_body={"restart": restart_index, "restart_reason": reason, "error": "cancelled"},
            duration_ms=round((time.monotonic() - chat_started) * 1000, 2),
        )
        raise
    except Exception as exc:
        await tracker.emit_chat_received(
            chat_correlation,
            status="failed",
            response_body={
                "restart": restart_index,
                "restart_reason": reason,
                "forced_convergence": reason == "forced_convergence",
                "error": str(exc),
            },
            duration_ms=round((time.monotonic() - chat_started) * 1000, 2),
        )
        return output


async def _run_forced_convergence(
    runner: Any,
    output: str,
    runtime_overrides: dict[str, Any],
    tracker: StageEventTracker,
    stage_name: str | None = None,
) -> str:
    if not tracker.should_force_convergence():
        return output

    tracker.mark_forced_convergence_used()
    prompt = _build_forced_convergence_prompt(stage_name or tracker.stage_name)
    chat_started = time.monotonic()
    chat_correlation = await tracker.emit_chat_sent(
        request_body={
            "prompt": prompt,
            "model": getattr(getattr(runner, "config", None), "model", None),
            "stage": tracker.stage_name,
            "agent_role": tracker.agent_role,
            "temperature": runtime_overrides.get("temperature"),
            "max_tokens": runtime_overrides.get("max_tokens"),
            "forced_convergence": True,
            "timeout_seconds": settings.WORKER_STAGE_TIMEOUT,
        },
    )

    try:
        followup_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
        response = await asyncio.wait_for(
            runner.chat(prompt, reset=False, **followup_kwargs),
            timeout=settings.WORKER_STAGE_TIMEOUT,
        )
        forced_text = response.text_content or ""
        await tracker.emit_chat_received(
            chat_correlation,
            status="success",
            response_body={"forced_convergence": True, "content": forced_text},
            duration_ms=round((time.monotonic() - chat_started) * 1000, 2),
        )
        cleaned = output.replace(
            "[Max turns reached. Please continue the conversation.]",
            "",
        ).strip()
        return f"{cleaned}\n\n{forced_text}".strip() if cleaned else forced_text
    except asyncio.CancelledError:
        _clear_current_task_cancellation_state()
        await tracker.emit_chat_received(
            chat_correlation,
            status="cancelled",
            response_body={"forced_convergence": True, "error": "cancelled"},
            duration_ms=round((time.monotonic() - chat_started) * 1000, 2),
        )
        raise
    except Exception as exc:
        await tracker.emit_chat_received(
            chat_correlation,
            status="failed",
            response_body={"forced_convergence": True, "error": str(exc)},
            duration_ms=round((time.monotonic() - chat_started) * 1000, 2),
        )
        return output


async def _handle_continuations(
    runner: Any,
    output: str,
    runtime_overrides: dict[str, Any],
    tracker: StageEventTracker,
    stage_name: str | None = None,
    restart_context: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Follow up with continuation prompts when the LLM output was truncated."""
    _MAX_CONTINUATIONS = 3
    _TRUNCATION_SENTINEL = "Max turns reached"
    restarts = 0
    effective_stage_name = stage_name or tracker.stage_name

    if restart_context is None and _prefer_restart_continuations(effective_stage_name):
        restart_context = {"stage_name": effective_stage_name}

    if restart_context is None:
        continuations = 0
        output = await _run_forced_convergence(runner, output, runtime_overrides, tracker, effective_stage_name)

        while _TRUNCATION_SENTINEL in (output or "") and continuations < _MAX_CONTINUATIONS:
            continuations += 1
            continuation_started = time.monotonic()
            prompt = _build_continuation_prompt(effective_stage_name)
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
    else:
        if tracker.should_force_convergence():
            restarts += 1
            tracker.mark_forced_convergence_used()
            output = await _run_stage_restart(
                runner,
                output,
                runtime_overrides,
                tracker,
                reason="forced_convergence",
                restart_index=restarts,
                restart_context=restart_context,
            )

        while _TRUNCATION_SENTINEL in (output or "") and restarts < _MAX_CONTINUATIONS:
            restarts += 1
            output = await _run_stage_restart(
                runner,
                output,
                runtime_overrides,
                tracker,
                reason="truncation",
                restart_index=restarts,
                restart_context=restart_context,
            )

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
    preflight_summary: Optional[str] = None,
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
        preflight_summary=preflight_summary,
        retry_context=retry_context,
        custom_instruction=custom_instruction,
        gate_rejection_context=gate_rejection_context,
    )
    user_prompt = build_user_prompt(ctx)

    runtime_overrides = _build_runtime_overrides(agent, stage_model)
    stage_max_turns = _resolve_stage_max_turns(stage.agent_role, runtime_overrides["max_turns"])
    runner_factory = (
        get_agent_text_only
        if _is_signoff_stage(stage.stage_name) or _is_text_only_stage(stage.stage_name)
        else get_agent
    )
    runner = runner_factory(
        stage.agent_role,
        task_id,
        model=runtime_overrides["model"],
        temperature=runtime_overrides["temperature"],
        max_tokens=runtime_overrides["max_tokens"],
        max_turns=stage_max_turns,
        extra_skill_dirs=runtime_overrides["extra_skill_dirs"],
        system_prompt_append=runtime_overrides["system_prompt_append"],
    )
    _apply_runner_workspace_override(runner, workdir_override)
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
                    "max_turns": stage_max_turns,
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
                        temperature=runtime_overrides["temperature"],
                        max_tokens=runtime_overrides["max_tokens"],
                        max_turns=stage_max_turns,
                        extra_skill_dirs=runtime_overrides["extra_skill_dirs"],
                        system_prompt_append=runtime_overrides["system_prompt_append"],
                    )
                    _apply_runner_workspace_override(runner, workdir_override)
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
    restart_context = {
        "task_title": task.title,
        "task_description": task.description,
        "stage_name": stage.stage_name,
        "preflight_summary": preflight_summary,
    }

    output, total_tokens = await _handle_continuations(
        runner, output, runtime_overrides, tracker, stage.stage_name, restart_context
    )

    # Phase 2.2: Evaluator-optimizer loop (if configured for this stage)
    if evaluator_config and evaluator_config.get("enabled"):
        eval_type = evaluator_config.get("type", "self_assessment")
        if eval_type == "objective":
            output, total_tokens = await _run_objective_loop(
                runner, output, total_tokens, stage, runtime_overrides,
                evaluator_config, workdir_override,
            )
        else:
            output, total_tokens = await _run_evaluator_loop_inner(
                runner, output, total_tokens, stage, runtime_overrides,
                evaluator_config,
            )

    final_output = _resolve_stage_output_summary(
        stage.stage_name,
        output,
        tracker.get_completed_tool_runs(),
    )
    await _finalize_stage_success(
        session,
        task,
        stage,
        agent,
        final_output,
        total_tokens,
        elapsed,
    )
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


async def _run_objective_loop(
    runner: Any,
    output: str,
    total_tokens: int,
    stage: TaskStageModel,
    runtime_overrides: dict[str, Any],
    evaluator_config: dict,
    workdir: Optional[str] = None,
) -> tuple[str, int]:
    """Objective verification loop — run shell commands to validate output.

    Instead of asking the LLM to self-assess, this runs real commands
    (pytest, ruff, tsc, etc.) and feeds concrete failure details back
    to the agent for targeted fixes.

    evaluator_config: {
        "enabled": True,
        "type": "objective",
        "commands": ["ruff check .", "pytest tests/ --tb=short -q"],
        "success_criteria": "all_pass",
        "max_iterations": 5,
        "token_budget": 50000,
    }
    """
    from app.worker.verifier import build_fix_prompt, run_verification

    commands = evaluator_config.get("commands")
    if not commands or not workdir:
        logger.info(
            "Objective loop skipped for stage %s: commands=%s workdir=%s",
            stage.stage_name, bool(commands), bool(workdir),
        )
        return output, total_tokens

    max_iterations = evaluator_config.get(
        "max_iterations", settings.VERIFIER_MAX_ITERATIONS,
    )
    success_criteria = evaluator_config.get("success_criteria", "all_pass")
    token_budget = evaluator_config.get(
        "token_budget", settings.VERIFIER_TOKEN_BUDGET,
    )
    cmd_timeout = evaluator_config.get("cmd_timeout")

    tokens_at_start = total_tokens

    for iteration in range(1, max_iterations + 1):
        # Step 1: Run objective verification
        verify_result = await run_verification(
            commands, cwd=workdir,
            success_criteria=success_criteria,
            timeout=cmd_timeout,
        )

        logger.info(
            "Objective verify iteration %d/%d for stage %s: passed=%s metrics=%s",
            iteration, max_iterations, stage.stage_name,
            verify_result.passed, verify_result.metrics,
        )

        # Store pass rate as self_assessment_score for consistency
        stage.self_assessment_score = verify_result.metrics.get("pass_rate", 0.0)

        if verify_result.passed:
            logger.info(
                "Objective verification passed for stage %s on iteration %d",
                stage.stage_name, iteration,
            )
            break

        # Check token budget before requesting a fix
        tokens_used = total_tokens - tokens_at_start
        if tokens_used >= token_budget:
            logger.warning(
                "Objective loop token budget exhausted for stage %s "
                "(%d/%d tokens used after %d iterations)",
                stage.stage_name, tokens_used, token_budget, iteration,
            )
            break

        # Last iteration — no point requesting another fix
        if iteration == max_iterations:
            logger.warning(
                "Objective loop max iterations reached for stage %s",
                stage.stage_name,
            )
            break

        # Step 2: Ask agent to fix based on concrete failure details
        fix_prompt = build_fix_prompt(
            stage.stage_name, iteration, max_iterations, verify_result,
        )
        chat_kwargs = _chat_kwargs_for_runner(runner, runtime_overrides)
        try:
            fix_response = await asyncio.wait_for(
                runner.chat(fix_prompt, reset=False, **chat_kwargs),
                timeout=settings.WORKER_STAGE_TIMEOUT,
            )
            output = fix_response.text_content
            total_tokens = runner.cumulative_usage.total_tokens
        except Exception:
            logger.warning(
                "Objective fix prompt failed for stage %s iteration %d",
                stage.stage_name, iteration, exc_info=True,
            )
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
    preflight_summary: Optional[str] = None,
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
        preflight_summary=preflight_summary,
        retry_context=retry_context,
        custom_instruction=custom_instruction,
        gate_rejection_context=gate_rejection_context,
    )
    user_prompt = build_user_prompt(ctx)
    system_prompt = SYSTEM_PROMPTS.get(stage.agent_role, SYSTEM_PROMPTS["orchestrator"])
    system_prompt += "\n\n你的工作目录是: /workspace\n所有文件操作请在此目录下进行。"

    runtime_overrides = _build_runtime_overrides(agent, stage_model)
    resolved_model = resolve_model_for_role(
        stage.agent_role,
        runtime_overrides["model"],
    )
    allowed_tools = list(ROLE_TOOLS.get(stage.agent_role, set()))

    # Resolve skill directories for the role
    from app.worker.agents import _get_skill_dirs
    skill_dirs = [f"/skills/{d.name}" for d in _get_skill_dirs(stage.agent_role)]

    max_turns = _resolve_stage_max_turns(stage.agent_role, runtime_overrides["max_turns"])

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
            "temperature": runtime_overrides.get("temperature"),
            "max_tokens": runtime_overrides.get("max_tokens"),
            "max_turns": max_turns,
            "stage": stage.stage_name,
            "agent_role": stage.agent_role,
            "prompt": user_prompt,
        },
        workspace="/workspace",
        execution_mode="sandbox",
        priority="high",
    )

    turn_runs: dict[str, dict[str, Any]] = {}
    tool_runs: dict[str, dict[str, Any]] = {}

    async def _handle_sandbox_event(stream_event: dict[str, Any]) -> None:
        event_type = str(stream_event.get("type") or "")
        data = stream_event.get("data")
        if not isinstance(data, dict):
            data = {}

        if event_type == "llm_turn_sent":
            turn = int(data.get("turn", 0) or 0)
            correlation_id = f"{chat_correlation}:turn:{turn}"
            log_id = await pipeline.emit_create(
                task_id=task_id,
                stage_id=stage_id,
                stage_name=stage.stage_name,
                agent_role=stage.agent_role,
                event_type="llm_turn_sent",
                event_source="llm",
                status="running",
                correlation_id=correlation_id,
                request_body={
                    "turn": turn,
                    "message_count": int(data.get("message_count", 0) or 0),
                },
                workspace="/workspace",
                execution_mode="sandbox",
                priority="high",
            )
            turn_runs[correlation_id] = {"log_id": log_id, "started": time.monotonic()}
            return

        if event_type == "llm_turn_received":
            turn = int(data.get("turn", 0) or 0)
            correlation_id = f"{chat_correlation}:turn:{turn}"
            run_info = turn_runs.pop(correlation_id, None)
            duration_ms = None
            if run_info is not None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
                await pipeline.emit_update(
                    log_id=run_info["log_id"],
                    updates={"status": "success", "duration_ms": duration_ms},
                    priority="high",
                )
            await pipeline.emit_create(
                task_id=task_id,
                stage_id=stage_id,
                stage_name=stage.stage_name,
                agent_role=stage.agent_role,
                event_type="llm_turn_received",
                event_source="llm",
                status="success",
                correlation_id=correlation_id,
                response_body={
                    "turn": turn,
                    "has_tool_calls": bool(data.get("has_tool_calls", False)),
                    "tool_call_count": int(data.get("tool_call_count", 0) or 0),
                    "content": str(data.get("content", "")),
                },
                duration_ms=duration_ms,
                workspace="/workspace",
                execution_mode="sandbox",
                priority="high",
            )
            return

        if event_type == "tool_call_started":
            tool_call_id = str(data.get("tool_call_id", "")).strip()
            if not tool_call_id:
                return
            tool_name = str(data.get("tool_name", ""))
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            workspace = str(args.get("cwd") or "/workspace").strip() or "/workspace"
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
                execution_mode="sandbox",
                priority="high",
            )
            tool_runs[tool_call_id] = {
                "log_id": log_id,
                "started": time.monotonic(),
                "summary": "",
                "truncated": False,
            }
            return

        if event_type == "tool_output":
            tool_call_id = str(data.get("tool_call_id", "")).strip()
            if not tool_call_id:
                return
            chunk = str(data.get("chunk", ""))
            run_info = tool_runs.get(tool_call_id)
            if run_info is None:
                return
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
            return

        if event_type == "tool_call_finished":
            tool_call_id = str(data.get("tool_call_id", "")).strip()
            if not tool_call_id:
                return
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            workspace = str(args.get("cwd") or "/workspace").strip() or "/workspace"
            tool_name = str(data.get("tool_name", ""))
            result_text = str(data.get("result", ""))
            raw_status = str(data.get("status", "")).strip().lower()
            status = (
                raw_status
                if raw_status in {"success", "failed", "cancelled"}
                else infer_tool_status(result_text)
            )
            run_info = tool_runs.pop(tool_call_id, None)
            if run_info is None:
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
                    execution_mode="sandbox",
                    result=result_text,
                    output_summary=result_text,
                    priority="high",
                )
                return

            duration_ms = _float_or_none(data.get("duration_ms"))
            if duration_ms is None:
                duration_ms = round((time.monotonic() - run_info["started"]) * 1000, 2)
            output_summary = run_info["summary"] or result_text
            await pipeline.emit_update(
                log_id=run_info["log_id"],
                updates={
                    "status": status,
                    "duration_ms": duration_ms,
                    "result": result_text,
                    "output_summary": output_summary,
                    "output_truncated": bool(run_info.get("truncated", False)),
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
                },
            )
            return

    # 6. Send to sandbox container
    sandbox_mgr = get_sandbox_manager()
    sandbox_result = await sandbox_mgr.execute_stage(
        sandbox_info,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=resolved_model,
        temperature=runtime_overrides.get("temperature"),
        max_tokens=runtime_overrides.get("max_tokens"),
        max_turns=max_turns,
        enable_tools=True,
        allowed_tools=allowed_tools,
        skill_dirs=skill_dirs,
        workdir="/workspace",
        timeout=int(settings.WORKER_STAGE_TIMEOUT),
        on_event=_handle_sandbox_event,
    )

    elapsed = time.monotonic() - start_time

    trailing_status = "failed" if sandbox_result.error else "cancelled"
    for correlation_id, run_info in list(turn_runs.items()):
        await pipeline.emit_update(
            log_id=run_info["log_id"],
            updates={
                "status": trailing_status,
                "duration_ms": round((time.monotonic() - run_info["started"]) * 1000, 2),
            },
            priority="high",
        )
        turn_runs.pop(correlation_id, None)
    for tool_call_id, run_info in list(tool_runs.items()):
        await pipeline.emit_update(
            log_id=run_info["log_id"],
            updates={
                "status": trailing_status,
                "duration_ms": round((time.monotonic() - run_info["started"]) * 1000, 2),
                "output_summary": run_info.get("summary") or "",
                "output_truncated": bool(run_info.get("truncated", False)),
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
            },
        )
        tool_runs.pop(tool_call_id, None)

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
    sandbox_tool_runs: list[dict[str, str]] = []

    # Fallback for legacy sandbox endpoint: tool calls are only available at completion.
    if not getattr(sandbox_result, "streamed", False):
        for index, tc in enumerate(sandbox_result.tool_calls):
            tool_name = str(tc.get("tool_name") or "")
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            workspace = str(args.get("cwd") or "/workspace").strip() or "/workspace"
            result_preview = str(tc.get("result_preview") or "")
            raw_status = str(tc.get("status") or "").strip().lower()
            status = (
                raw_status
                if raw_status in {"success", "failed", "cancelled"}
                else infer_tool_status(result_preview)
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
            sandbox_tool_runs.append(
                {
                    "status": status,
                    "command": _summarize_tool_command(tool_name, args),
                    "result_preview": result_preview,
                }
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
    stage.output_summary = _resolve_stage_output_summary(
        stage.stage_name,
        output,
        sandbox_tool_runs,
    )
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
