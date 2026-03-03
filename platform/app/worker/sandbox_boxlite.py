"""BoxLite sandbox backend — direct in-process execution via micro-VM.

Instead of launching a Docker container with an HTTP agent server,
this backend runs the AgentRunner directly in the host process while
routing ``execute``/``execute_script``/``read``/``write`` tool calls
through a :class:`BoxLiteRuntime` micro-VM.

Key benefits over Docker:
- API keys stay in the host process (never enter the sandbox)
- No HTTP serialization overhead
- Millisecond-level sandbox startup
- Native Python event bus (no NDJSON bridging)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.worker.sandbox_backend import (
    OnSandboxEvent,
    SandboxCreateResult,
    SandboxInfo,
    SandboxResult,
)

logger = logging.getLogger(__name__)

# Semaphore to limit concurrent boxes
_concurrency_sem: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _concurrency_sem
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(settings.SANDBOX_MAX_CONCURRENT)
    return _concurrency_sem


class BoxLiteSandboxBackend:
    """Sandbox backend that uses BoxLite micro-VMs for execution isolation.

    The LLM client runs in-process on the host.  Only shell commands and
    file I/O are dispatched into the BoxLite VM via ``SandboxedAgentRunner``.
    """

    def __init__(self) -> None:
        self._boxes: dict[str, Any] = {}  # task_id → BoxLiteRuntime

    # ------------------------------------------------------------------
    # SandboxBackend protocol
    # ------------------------------------------------------------------

    async def create(
        self,
        task_id: str,
        *,
        workspace: str,
        workspace_source: str = "fallback",
        image: Optional[str] = None,
    ) -> SandboxCreateResult:
        """Create a BoxLite micro-VM for a task."""
        from skillkit.runtime.boxlite import BoxLiteRuntime  # noqa: PLC0415

        def _failed(*, error_code: str, error_message: str) -> SandboxCreateResult:
            logger.error("BoxLite create failed (%s): %s", error_code, error_message)
            return SandboxCreateResult(
                info=None,
                workspace=workspace,
                workspace_source=workspace_source,
                error_code=error_code,
                error_message=error_message,
            )

        workspace_path = Path(workspace)
        if not workspace_path.exists() or not workspace_path.is_dir():
            return _failed(
                error_code="workspace_not_found",
                error_message=f"Workspace path does not exist: {workspace}",
            )

        sem = _get_semaphore()
        await sem.acquire()

        resolved_image = image or settings.SANDBOX_IMAGE
        sandbox_name = f"sa-boxlite-{task_id[:12]}"

        try:
            runtime = BoxLiteRuntime(
                image=resolved_image,
                memory_mib=getattr(settings, "SANDBOX_MEMORY_MIB", 4096),
                cpus=int(settings.SANDBOX_CPUS),
                volumes=[(workspace, "/workspace", "rw")],
                working_dir="/workspace",
                auto_destroy=False,
            )
            await runtime.start()

            if not await runtime.is_ready():
                await runtime.destroy()
                sem.release()
                return _failed(
                    error_code="box_unhealthy",
                    error_message=f"BoxLite VM {sandbox_name} failed readiness check",
                )
        except Exception as e:
            sem.release()
            return _failed(
                error_code="box_create_failed",
                error_message=str(e),
            )

        info = SandboxInfo(
            task_id=task_id,
            sandbox_name=sandbox_name,
            extra={"runtime": runtime},
        )
        self._boxes[task_id] = runtime

        logger.info("BoxLite sandbox ready: %s (image=%s)", sandbox_name, resolved_image)
        return SandboxCreateResult(
            info=info,
            workspace=workspace,
            workspace_source=workspace_source,
        )

    async def execute_stage(
        self,
        info: SandboxInfo,
        *,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_turns: int = 20,
        enable_tools: bool = True,
        allowed_tools: Optional[list[str]] = None,
        skill_dirs: Optional[list[str]] = None,
        workdir: str = "/workspace",
        timeout: int = 300,
        on_event: Optional[OnSandboxEvent] = None,
    ) -> SandboxResult:
        """Execute a stage in-process, routing tools through BoxLiteRuntime."""
        from skillkit import AgentConfig, SkillsConfig, SkillsEngine  # noqa: PLC0415
        from skillkit.sandbox.runner import SandboxedAgentRunner  # noqa: PLC0415

        runtime = info.extra.get("runtime")
        if runtime is None:
            return SandboxResult(error="BoxLiteRuntime not found in SandboxInfo.extra")

        logger.info(
            "Executing stage in BoxLite sandbox %s (model=%s, max_turns=%d, timeout=%ds)",
            info.sandbox_name,
            model or "default",
            max_turns,
            timeout,
        )

        try:
            # Build engine with BoxLiteRuntime
            config = SkillsConfig(
                skill_dirs=skill_dirs or [],
            )
            engine = SkillsEngine(config=config, runtime=runtime)

            # Build agent config
            agent_config = AgentConfig(
                model=model or settings.LLM_MODEL,
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                system_prompt=system_prompt,
                max_turns=max_turns,
                enable_tools=enable_tools,
                load_context_files=False,
            )
            if temperature is not None:
                agent_config.temperature = temperature
            if max_tokens is not None:
                agent_config.max_tokens = max_tokens

            runner = SandboxedAgentRunner(
                engine=engine,
                config=agent_config,
                box_runtime=runtime,
            )

            # Register event handlers for streaming
            cleanup_fn = _register_event_bridge(runner, on_event)

            try:
                response = await asyncio.wait_for(
                    runner.chat(user_prompt, reset=True),
                    timeout=timeout,
                )
            finally:
                cleanup_fn()

            text_content = response.text_content if response else ""
            total_tokens = 0
            if hasattr(runner, "cumulative_usage") and runner.cumulative_usage:
                total_tokens = runner.cumulative_usage.total_tokens

            return SandboxResult(
                text_content=text_content,
                total_tokens=total_tokens,
                tool_calls=getattr(runner, "tool_calls_log", []),
                error=None,
                streamed=True,
            )
        except asyncio.TimeoutError:
            return SandboxResult(
                error=f"Stage execution timed out after {timeout}s in {info.sandbox_name}",
                streamed=True,
            )
        except Exception as e:
            logger.exception("BoxLite stage execution failed for %s", info.sandbox_name)
            return SandboxResult(error=f"BoxLite execution error: {e}", streamed=True)

    async def destroy(self, task_id: str) -> None:
        """Destroy a task's BoxLite VM."""
        runtime = self._boxes.pop(task_id, None)
        if runtime is not None:
            try:
                await runtime.destroy()
            except Exception:
                logger.warning("Failed to destroy BoxLite VM for task %s", task_id, exc_info=True)
            _get_semaphore().release()
            logger.info("Destroyed BoxLite sandbox for task %s", task_id)

    async def destroy_all(self) -> None:
        """Destroy all managed BoxLite VMs."""
        task_ids = list(self._boxes.keys())
        for task_id in task_ids:
            await self.destroy(task_id)

    def get_info(self, task_id: str) -> Optional[SandboxInfo]:
        runtime = self._boxes.get(task_id)
        if runtime is None:
            return None
        return SandboxInfo(
            task_id=task_id,
            sandbox_name=f"sa-boxlite-{task_id[:12]}",
            extra={"runtime": runtime},
        )


# ---------------------------------------------------------------------------
# Event bridge: SkillKit EventBus → on_event callback (NDJSON-compatible)
# ---------------------------------------------------------------------------


def _register_event_bridge(
    runner: Any,
    on_event: Optional[OnSandboxEvent],
) -> Any:
    """Map SkillKit runner events to the platform's sandbox event format.

    Reproduces the same event types as ``agent_server._register_stream_handlers``
    so that ``executor._handle_sandbox_event`` works unchanged.
    """
    if on_event is None:
        return lambda: None

    events = getattr(runner, "events", None)
    if events is None or not hasattr(events, "on"):
        return lambda: None

    source = f"boxlite-bridge-{time.time_ns()}"

    async def _emit(event_type: str, data: dict[str, Any]) -> None:
        result = on_event({"type": event_type, "data": data})
        if inspect.isawaitable(result):
            await result

    async def _on_turn_start(event: Any) -> None:
        await _emit(
            "llm_turn_sent",
            {
                "turn": int(getattr(event, "turn", 0)),
                "message_count": int(getattr(event, "message_count", 0)),
            },
        )

    async def _on_turn_end(event: Any) -> None:
        await _emit(
            "llm_turn_received",
            {
                "turn": int(getattr(event, "turn", 0)),
                "has_tool_calls": bool(getattr(event, "has_tool_calls", False)),
                "tool_call_count": int(getattr(event, "tool_call_count", 0)),
                "content": str(getattr(event, "content", "")),
            },
        )

    async def _on_before_tool_call(event: Any) -> None:
        args = getattr(event, "args", {}) or {}
        if not isinstance(args, dict):
            args = {}
        await _emit(
            "tool_call_started",
            {
                "tool_call_id": str(getattr(event, "tool_call_id", "")),
                "tool_name": str(getattr(event, "tool_name", "")),
                "args": args,
            },
        )

    async def _on_tool_execution_update(event: Any) -> None:
        await _emit(
            "tool_output",
            {
                "tool_call_id": str(getattr(event, "tool_call_id", "")),
                "chunk": str(getattr(event, "output", "")),
            },
        )

    async def _on_after_tool_result(event: Any) -> None:
        args = getattr(event, "args", {}) or {}
        if not isinstance(args, dict):
            args = {}
        result = str(getattr(event, "result", ""))
        await _emit(
            "tool_call_finished",
            {
                "tool_call_id": str(getattr(event, "tool_call_id", "")),
                "tool_name": str(getattr(event, "tool_name", "")),
                "args": args,
                "result": result,
                "status": (
                    "failed"
                    if result.startswith(("Error:", "Exit code:"))
                    else "success"
                ),
            },
        )

    events.on("turn_start", _on_turn_start, source=source)
    events.on("turn_end", _on_turn_end, source=source)
    events.on("before_tool_call", _on_before_tool_call, source=source)
    events.on("tool_execution_update", _on_tool_execution_update, source=source)
    events.on("after_tool_result", _on_after_tool_result, source=source)

    def _cleanup() -> None:
        if hasattr(events, "off_by_source"):
            events.off_by_source(source)

    return _cleanup
