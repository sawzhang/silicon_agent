"""Lightweight agent server that runs inside sandbox containers.

Receives stage execution requests via HTTP, creates a SkillKit AgentRunner,
runs the full LLM chat loop with tools, and returns structured results.

Usage inside container:
    python agent_server.py --port 9090
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sandbox-agent")

# SkillKit is installed inside the container image
try:
    from skillkit import AgentRunner
except ImportError:
    logger.error("SkillKit not available in container — cannot execute agent stages")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Sandboxed runner with tool filtering (mirrors platform SandboxedAgentRunner)
# ---------------------------------------------------------------------------
_ALL_TOOLS = {"read", "write", "execute", "execute_script", "skill"}


def _normalize_openai_base_url(base_url: str | None) -> str:
    value = (base_url or "").strip()
    if not value:
        return ""
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def _hydrate_skillkit_env_from_llm_env() -> list[str]:
    """Fallback compatibility for mixed platform/container versions.

    SkillKit reads OPENAI_* / MINIMAX_MODEL, while platform config is LLM_*.
    """
    applied: list[str] = []
    llm_api_key = os.environ.get("LLM_API_KEY")
    llm_base_url = os.environ.get("LLM_BASE_URL")
    llm_model = os.environ.get("LLM_MODEL")

    if not os.environ.get("OPENAI_API_KEY") and llm_api_key:
        os.environ["OPENAI_API_KEY"] = llm_api_key
        applied.append("OPENAI_API_KEY")

    if not os.environ.get("OPENAI_BASE_URL") and llm_base_url:
        os.environ["OPENAI_BASE_URL"] = _normalize_openai_base_url(llm_base_url)
        applied.append("OPENAI_BASE_URL")

    if not os.environ.get("MINIMAX_MODEL") and llm_model:
        os.environ["MINIMAX_MODEL"] = llm_model
        applied.append("MINIMAX_MODEL")

    return applied


class ContainerAgentRunner(AgentRunner):
    """AgentRunner with tool filtering and cwd injection, running inside the container."""

    def __init__(
        self,
        *args,
        default_cwd: str | None = None,
        allowed_tools: set[str] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.default_cwd = default_cwd
        self.allowed_tools = allowed_tools or _ALL_TOOLS
        self.tool_calls_log: list[dict[str, Any]] = []

    def get_tools(self):
        tools = super().get_tools()
        return [t for t in tools if t["function"]["name"] in self.allowed_tools]

    async def _execute_tool(self, tool_call, on_output=None):
        name = tool_call.get("name", "")
        started = time.monotonic()

        # Inject default cwd for execution tools
        if self.default_cwd and name in ("execute", "execute_script"):
            args = json.loads(tool_call.get("arguments", "{}"))
            if not args.get("cwd"):
                args["cwd"] = self.default_cwd
                tool_call = {**tool_call, "arguments": json.dumps(args)}

        # Block disallowed tools
        if name not in self.allowed_tools:
            return f"Error: {name} is not allowed for this role"

        result = await super()._execute_tool(tool_call, on_output)

        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        args = json.loads(tool_call.get("arguments", "{}"))
        self.tool_calls_log.append(
            {
                "tool_name": name,
                "args": args,
                "duration_ms": elapsed_ms,
                "result_preview": str(result)[:500] if result else "",
                "status": (
                    "failed"
                    if str(result).startswith(("Error:", "Exit code:"))
                    else "success"
                ),
            }
        )

        return result


# ---------------------------------------------------------------------------
# Stage execution helpers
# ---------------------------------------------------------------------------
def _parse_request_body(body: dict[str, Any]) -> dict[str, Any]:
    system_prompt = str(body.get("system_prompt", ""))
    user_prompt = str(body.get("user_prompt", ""))
    model = body.get("model")
    temperature_raw = body.get("temperature")
    temperature = float(temperature_raw) if temperature_raw is not None else None
    max_tokens_raw = body.get("max_tokens")
    max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None
    max_turns = int(body.get("max_turns", 20) or 20)
    enable_tools = bool(body.get("enable_tools", True))
    allowed_tools = set(body.get("allowed_tools", list(_ALL_TOOLS)))
    skill_dirs_raw = body.get("skill_dirs", [])
    workdir = str(body.get("workdir", "/workspace") or "/workspace")
    timeout = int(body.get("timeout", 300) or 300)
    skill_dirs = [Path(d) for d in skill_dirs_raw if Path(d).is_dir()]
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_turns": max_turns,
        "enable_tools": enable_tools,
        "allowed_tools": allowed_tools,
        "skill_dirs": skill_dirs,
        "workdir": workdir,
        "timeout": timeout,
    }


def _create_runner(parsed: dict[str, Any]) -> ContainerAgentRunner:
    create_kwargs: dict[str, Any] = {
        "skill_dirs": parsed["skill_dirs"],
        "system_prompt": parsed["system_prompt"],
        "max_turns": parsed["max_turns"],
        "enable_tools": parsed["enable_tools"],
        "load_context_files": False,
    }
    base = AgentRunner.create(**create_kwargs)
    if parsed["model"]:
        # SkillKit's AgentConfig.from_env already sets model internally.
        # Override after creation to avoid duplicate keyword collisions.
        base.config.model = parsed["model"]
    if parsed["temperature"] is not None:
        # Keep runtime override handling consistent with model override.
        base.config.temperature = parsed["temperature"]
    if parsed["max_tokens"] is not None:
        # Keep runtime override handling consistent with model override.
        base.config.max_tokens = parsed["max_tokens"]
    runner = ContainerAgentRunner(
        engine=base.engine,
        config=base.config,
        default_cwd=parsed["workdir"],
        allowed_tools=parsed["allowed_tools"] if parsed["enable_tools"] else set(),
    )
    return runner


async def _run_stage_chat(
    runner: ContainerAgentRunner,
    *,
    user_prompt: str,
    timeout: int,
) -> dict[str, Any]:
    response = await asyncio.wait_for(
        runner.chat(user_prompt, reset=True),
        timeout=timeout,
    )
    text_content = response.text_content or ""
    total_tokens = runner.cumulative_usage.total_tokens

    # Handle truncated output (max turns)
    max_continuations = 3
    sentinel = "Max turns reached"
    continuations = 0
    while sentinel in text_content and continuations < max_continuations:
        continuations += 1
        logger.info(
            "Output truncated, sending continuation %d/%d",
            continuations,
            max_continuations,
        )
        try:
            cont = await asyncio.wait_for(
                runner.chat("请继续完成上面的输出，从你停下的地方继续。", reset=False),
                timeout=timeout,
            )
            cont_text = cont.text_content or ""
            text_content = text_content.replace(
                f"[{sentinel}. Please continue the conversation.]",
                "",
            ).strip()
            text_content = f"{text_content}\n\n{cont_text}".strip()
            total_tokens = runner.cumulative_usage.total_tokens
        except Exception as e:
            logger.warning("Continuation %d failed: %s", continuations, e)
            break

    logger.info(
        "Stage completed: %d tokens, %d tool calls",
        total_tokens,
        len(runner.tool_calls_log),
    )
    return {
        "text_content": text_content,
        "total_tokens": total_tokens,
        "tool_calls": runner.tool_calls_log,
        "error": None,
    }


def _register_stream_handlers(
    runner: ContainerAgentRunner,
    event_queue: asyncio.Queue[dict[str, Any]],
):
    events = getattr(runner, "events", None)
    if events is None or not hasattr(events, "on"):
        return lambda: None

    source = f"sandbox-stream-{time.time_ns()}"

    async def _emit(event_type: str, data: dict[str, Any]) -> None:
        await event_queue.put({"type": event_type, "data": data})

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


# ---------------------------------------------------------------------------
# Stage execution endpoints
# ---------------------------------------------------------------------------
async def handle_execute(request: web.Request) -> web.Response:
    """Execute a stage and return final payload in one response."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    parsed = _parse_request_body(body)
    logger.info(
        "Executing stage: model=%s temperature=%s max_tokens=%s max_turns=%d tools=%s workdir=%s timeout=%ds",
        parsed["model"] or "default",
        parsed["temperature"],
        parsed["max_tokens"],
        parsed["max_turns"],
        sorted(parsed["allowed_tools"]),
        parsed["workdir"],
        parsed["timeout"],
    )

    try:
        runner = _create_runner(parsed)
    except Exception as e:
        logger.exception("Failed to create AgentRunner")
        return web.json_response(
            {
                "text_content": "",
                "total_tokens": 0,
                "tool_calls": [],
                "error": f"AgentRunner creation failed: {e}",
            },
            status=500,
        )

    try:
        payload = await _run_stage_chat(
            runner,
            user_prompt=parsed["user_prompt"],
            timeout=parsed["timeout"],
        )
        return web.json_response(payload)
    except asyncio.TimeoutError:
        logger.error("Stage execution timed out after %ds", parsed["timeout"])
        return web.json_response(
            {
                "text_content": "",
                "total_tokens": (
                    runner.cumulative_usage.total_tokens
                    if hasattr(runner, "cumulative_usage")
                    else 0
                ),
                "tool_calls": runner.tool_calls_log,
                "error": f"Stage timed out after {parsed['timeout']}s",
            },
            status=504,
        )
    except Exception as e:
        logger.exception("Stage execution failed")
        return web.json_response(
            {
                "text_content": "",
                "total_tokens": 0,
                "tool_calls": (
                    runner.tool_calls_log if hasattr(runner, "tool_calls_log") else []
                ),
                "error": str(e),
            },
            status=500,
        )


async def handle_execute_stream(request: web.Request) -> web.StreamResponse:
    """Execute a stage and stream incremental events as NDJSON."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    parsed = _parse_request_body(body)
    logger.info(
        "Executing stage stream: model=%s temperature=%s max_tokens=%s max_turns=%d tools=%s workdir=%s timeout=%ds",
        parsed["model"] or "default",
        parsed["temperature"],
        parsed["max_tokens"],
        parsed["max_turns"],
        sorted(parsed["allowed_tools"]),
        parsed["workdir"],
        parsed["timeout"],
    )

    try:
        runner = _create_runner(parsed)
    except Exception as e:
        logger.exception("Failed to create AgentRunner for stream")
        return web.json_response(
            {
                "error": f"AgentRunner creation failed: {e}",
            },
            status=500,
        )

    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    cleanup_handlers = _register_stream_handlers(runner, event_queue)

    async def _run_with_final_event() -> None:
        try:
            final_payload = await _run_stage_chat(
                runner,
                user_prompt=parsed["user_prompt"],
                timeout=parsed["timeout"],
            )
        except asyncio.TimeoutError:
            final_payload = {
                "text_content": "",
                "total_tokens": (
                    runner.cumulative_usage.total_tokens
                    if hasattr(runner, "cumulative_usage")
                    else 0
                ),
                "tool_calls": runner.tool_calls_log,
                "error": f"Stage timed out after {parsed['timeout']}s",
            }
        except Exception as e:
            logger.exception("Stage stream execution failed")
            final_payload = {
                "text_content": "",
                "total_tokens": 0,
                "tool_calls": (
                    runner.tool_calls_log if hasattr(runner, "tool_calls_log") else []
                ),
                "error": str(e),
            }
        finally:
            cleanup_handlers()

        await event_queue.put({"type": "final", "data": final_payload})

    run_task = asyncio.create_task(_run_with_final_event())

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    try:
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                heartbeat = {"type": "heartbeat", "data": {"ts": time.time()}}
                await response.write(
                    (json.dumps(heartbeat, ensure_ascii=False) + "\n").encode("utf-8")
                )
                continue

            await response.write(
                (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            )
            if event.get("type") == "final":
                break
    except (ConnectionResetError, asyncio.CancelledError):
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
    finally:
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
        with contextlib.suppress(ConnectionResetError):
            await response.write_eof()

    return response


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok", "agent": "sandbox"})


def create_app() -> web.Application:
    hydrated = _hydrate_skillkit_env_from_llm_env()
    if hydrated:
        logger.info("Hydrated SkillKit env from LLM_* fallback keys: %s", hydrated)
    app = web.Application()
    app.router.add_post("/execute", handle_execute)
    app.router.add_post("/execute_stream", handle_execute_stream)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sandbox Agent Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "9090")))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    logger.info("Starting sandbox agent server on %s:%d", args.host, args.port)
    web.run_app(create_app(), host=args.host, port=args.port)
