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
import re
import shlex
import sys
import time
from datetime import datetime, timezone
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

try:
    from sandbox.tool_policy import (
        DEFAULT_FALLBACK_CORE_TOOLS,
        DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS,
        ToolExecutionPolicyMixin,
        discover_tool_catalog,
        sanitize_requested_tools,
    )
except ImportError:
    from tool_policy import (  # type: ignore[no-redef]
        DEFAULT_FALLBACK_CORE_TOOLS,
        DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS,
        ToolExecutionPolicyMixin,
        discover_tool_catalog,
        sanitize_requested_tools,
    )


# ---------------------------------------------------------------------------
# Sandboxed runner with tool filtering (mirrors platform SandboxedAgentRunner)
# ---------------------------------------------------------------------------
_ALL_TOOLS: set[str] = set()
_TOOL_ARGUMENT_HINTS: dict[str, str] = {}
_JAVA_DETECT_FILES = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle.properties",
)
_JAVA8_PATTERNS = (
    r"<java\.version>\s*(?:1\.8|8)\s*</java\.version>",
    r"<maven\.compiler\.(?:source|target|release)>\s*(?:1\.8|8)\s*</maven\.compiler\.(?:source|target|release)>",
    r"sourceCompatibility\s*=\s*(?:['\"]?1\.8['\"]?|JavaVersion\.VERSION_1_8)",
    r"targetCompatibility\s*=\s*(?:['\"]?1\.8['\"]?|JavaVersion\.VERSION_1_8)",
)
_JAVA17_PATTERNS = (
    r"<java\.version>\s*17\s*</java\.version>",
    r"<maven\.compiler\.(?:source|target|release)>\s*17\s*</maven\.compiler\.(?:source|target|release)>",
    r"sourceCompatibility\s*=\s*(?:['\"]?17['\"]?|JavaVersion\.VERSION_17)",
    r"targetCompatibility\s*=\s*(?:['\"]?17['\"]?|JavaVersion\.VERSION_17)",
)
_GRADLE_WRAPPER_CMD_RE = re.compile(r"(?<![\w./-])(?:sh\s+)?(?:\./)?gradlew(?![\w.-])")
_GRADLE_ANY_CMD_RE = re.compile(r"(?<![\w./-])(?:gradle|(?:sh\s+)?(?:\./)?gradlew)(?![\w.-])")
_RUNTIME_PREFLIGHT_DONE = False
_RUNTIME_PREFLIGHT_LOCK = asyncio.Lock()


def _normalize_openai_base_url(base_url: str | None) -> str:
    value = (base_url or "").strip()
    if not value:
        return ""
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def _detect_java_major_version(workdir: str) -> int | None:
    workspace = Path(workdir)
    if not workspace.exists():
        return None

    scanned: list[str] = []
    for rel in _JAVA_DETECT_FILES:
        file_path = workspace / rel
        if not file_path.is_file():
            continue
        with contextlib.suppress(Exception):
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            scanned.append(text[:200_000])

    if not scanned:
        return None

    combined = "\n".join(scanned)
    if any(re.search(pattern, combined, re.IGNORECASE) for pattern in _JAVA8_PATTERNS):
        return 8
    if any(re.search(pattern, combined, re.IGNORECASE) for pattern in _JAVA17_PATTERNS):
        return 17
    return None


def _configure_java_runtime_for_workspace(workdir: str) -> int | None:
    major = _detect_java_major_version(workdir)
    if major is None:
        return None

    java_home_key = "JAVA8_HOME" if major == 8 else "JAVA17_HOME"
    target_java_home = (os.environ.get(java_home_key) or "").strip()
    if not target_java_home:
        logger.warning("Java %s requested but %s is not configured", major, java_home_key)
        return major

    current_path = os.environ.get("PATH", "")
    path_parts = [p for p in current_path.split(":") if p]
    path_parts = [
        p for p in path_parts
        if not p.startswith("/opt/jdk8/bin")
        and not p.startswith("/opt/jdk17/bin")
    ]
    os.environ["JAVA_HOME"] = target_java_home
    os.environ["PATH"] = ":".join([f"{target_java_home}/bin", *path_parts])
    logger.info(
        "Configured Java runtime for workspace %s: java=%s JAVA_HOME=%s",
        workdir,
        major,
        target_java_home,
    )
    return major


def _is_gemini_model(model: str | None) -> bool:
    return "gemini" in ((model or "").lower())


def _sanitize_reasoning_kwargs_for_model(
    model: str | None,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Gemini compatibility: remove unsupported reasoning_split field."""
    if not _is_gemini_model(model):
        return kwargs

    extra_body = kwargs.get("extra_body")
    if not isinstance(extra_body, dict) or "reasoning_split" not in extra_body:
        return kwargs

    sanitized_kwargs = dict(kwargs)
    sanitized_extra = dict(extra_body)
    sanitized_extra.pop("reasoning_split", None)
    if sanitized_extra:
        sanitized_kwargs["extra_body"] = sanitized_extra
    else:
        sanitized_kwargs.pop("extra_body", None)
    return sanitized_kwargs


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _rewrite_gradle_wrapper_command(command: str) -> tuple[str, bool]:
    rewritten, count = _GRADLE_WRAPPER_CMD_RE.subn("gradle", command)
    return rewritten, count > 0


def _build_gradle_command(
    *,
    original_command: str,
    rewritten_command: str,
    timeout_seconds: int,
    allow_wrapper_fallback: bool,
) -> tuple[str, str]:
    payload = rewritten_command
    strategy = "system"
    if allow_wrapper_fallback and rewritten_command != original_command:
        strategy = "wrapper_fallback"
        payload = (
            f"{rewritten_command}; __sa_rc=$?; "
            f"if [ $__sa_rc -eq 126 ] || [ $__sa_rc -eq 127 ]; then {original_command}; "
            f"else exit $__sa_rc; fi"
        )
    if timeout_seconds > 0:
        payload = f"timeout {timeout_seconds}s bash -lc {shlex.quote(payload)}"
    return payload, strategy


async def _run_runtime_preflight_once() -> None:
    global _RUNTIME_PREFLIGHT_DONE
    if _RUNTIME_PREFLIGHT_DONE:
        return

    async with _RUNTIME_PREFLIGHT_LOCK:
        if _RUNTIME_PREFLIGHT_DONE:
            return

        async def _run_line(cmd: str, *, timeout: float = 5.0) -> str:
            proc = await asyncio.create_subprocess_exec(
                "sh", "-lc", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
                    await proc.communicate()
                return ""
            return (out or b"").decode("utf-8", errors="ignore").strip()

        gradle_line = await _run_line("gradle -v 2>&1 | head -n 5")
        java_line = await _run_line("java -version 2>&1 | head -n 3")
        if gradle_line:
            logger.info("sandbox_runtime_preflight gradle=%s", gradle_line.replace("\n", " | "))
        else:
            logger.info("sandbox_runtime_preflight gradle=unavailable")
        if java_line:
            logger.info("sandbox_runtime_preflight java=%s", java_line.replace("\n", " | "))
        else:
            logger.info("sandbox_runtime_preflight java=unavailable")

        _RUNTIME_PREFLIGHT_DONE = True


def _extract_gemini_thought_signatures_from_response(response_obj: Any) -> dict[str, str]:
    """Extract OpenAI-compat thought signatures keyed by tool_call id."""
    if hasattr(response_obj, "model_dump"):
        try:
            data = response_obj.model_dump(mode="json")
        except Exception:
            data = response_obj.model_dump()
    elif hasattr(response_obj, "to_dict"):
        data = response_obj.to_dict()
    else:
        data = response_obj

    if not isinstance(data, dict):
        return {}

    signatures: dict[str, str] = {}
    choices = data.get("choices")
    if not isinstance(choices, list):
        return signatures

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or "").strip()
            if not tool_call_id:
                continue
            extra = tool_call.get("extra_content")
            if not isinstance(extra, dict):
                continue
            google = extra.get("google")
            if not isinstance(google, dict):
                continue
            thought_signature = google.get("thought_signature")
            if isinstance(thought_signature, str) and thought_signature:
                signatures[tool_call_id] = thought_signature
    return signatures


def _inject_gemini_thought_signatures_into_messages(
    kwargs: dict[str, Any],
    signatures: dict[str, str],
) -> dict[str, Any]:
    """Inject required thought signatures back into assistant/tool_call history."""
    messages = kwargs.get("messages")
    if not isinstance(messages, list) or not signatures:
        return kwargs

    changed = False
    updated_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            updated_messages.append(message)
            continue

        role = str(message.get("role") or "")
        tool_calls = message.get("tool_calls")
        if role not in ("assistant", "model") or not isinstance(tool_calls, list):
            updated_messages.append(message)
            continue

        updated_tool_calls: list[Any] = []
        message_changed = False
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                updated_tool_calls.append(tool_call)
                continue
            tool_call_id = str(tool_call.get("id") or "").strip()
            expected_signature = signatures.get(tool_call_id)
            if not expected_signature:
                updated_tool_calls.append(tool_call)
                continue

            extra = tool_call.get("extra_content")
            if isinstance(extra, dict) and isinstance(extra.get("google"), dict):
                existing_signature = extra["google"].get("thought_signature")
                if isinstance(existing_signature, str) and existing_signature:
                    updated_tool_calls.append(tool_call)
                    continue

            updated_tool_call = dict(tool_call)
            updated_extra = dict(updated_tool_call.get("extra_content") or {})
            updated_google = dict(updated_extra.get("google") or {})
            updated_google["thought_signature"] = expected_signature
            updated_extra["google"] = updated_google
            updated_tool_call["extra_content"] = updated_extra
            updated_tool_calls.append(updated_tool_call)
            message_changed = True
            changed = True

        if message_changed:
            updated_message = dict(message)
            updated_message["tool_calls"] = updated_tool_calls
            updated_messages.append(updated_message)
        else:
            updated_messages.append(message)

    if not changed:
        return kwargs

    updated_kwargs = dict(kwargs)
    updated_kwargs["messages"] = updated_messages
    return updated_kwargs


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


def _create_tool_probe_runner():
    skill_dirs = []
    shared = Path("/skills/shared")
    if shared.is_dir():
        skill_dirs.append(shared)
    return AgentRunner.create(
        skill_dirs=skill_dirs,
        system_prompt="",
        enable_tools=True,
        load_context_files=False,
        max_turns=1,
    )


_ALL_TOOLS, _TOOL_ARGUMENT_HINTS = discover_tool_catalog(
    create_probe_runner=_create_tool_probe_runner,
    fallback_core_tools=DEFAULT_FALLBACK_CORE_TOOLS,
    fallback_hints=DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS,
    logger=logger,
    warning_message="Failed to discover sandbox tool catalog from SkillKit; fallback to defaults",
)


class ContainerAgentRunner(ToolExecutionPolicyMixin, AgentRunner):
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
        self._tool_argument_hints = _TOOL_ARGUMENT_HINTS
        self.tool_calls_log: list[dict[str, Any]] = []
        # Default enabled for incident forensics; set SANDBOX_DUMP_MODEL_API_RESPONSE=false to disable.
        raw_dump_flag = os.environ.get("SANDBOX_DUMP_MODEL_API_RESPONSE", "true")
        self.dump_model_api_response = raw_dump_flag.strip().lower() == "true"
        self.model_api_raw_log_path = os.environ.get(
            "SANDBOX_MODEL_API_RAW_LOG_PATH",
            "/workspace/.agent_logs/model_api_raw_responses.jsonl",
        )
        self._gemini_tool_call_signatures: dict[str, str] = {}

    def get_tools(self):
        tools = super().get_tools()
        return [t for t in tools if t["function"]["name"] in self.allowed_tools]

    def _append_tool_call_log(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        started_at: float,
        result_preview: str,
        status: str,
    ) -> None:
        elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
        self.tool_calls_log.append(
            {
                "tool_name": tool_name,
                "args": args,
                "duration_ms": elapsed_ms,
                "result_preview": result_preview[:500],
                "status": status,
            }
        )

    @staticmethod
    def _to_jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            with contextlib.suppress(Exception):
                return value.model_dump(mode="json")
            with contextlib.suppress(Exception):
                return value.model_dump()
        if hasattr(value, "to_dict"):
            with contextlib.suppress(Exception):
                return value.to_dict()
        with contextlib.suppress(Exception):
            json.dumps(value, ensure_ascii=False)
            return value
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

    def _append_model_api_raw_log(self, *, request_kwargs: dict[str, Any], response_obj: Any) -> None:
        if not self.dump_model_api_response:
            return
        try:
            path = Path(self.model_api_raw_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts_utc": datetime.now(timezone.utc).isoformat(),
                "model": self.config.model,
                "llm_base_url": (
                    self.config.base_url
                    or os.environ.get("OPENAI_BASE_URL")
                    or os.environ.get("LLM_BASE_URL")
                    or ""
                ),
                "request": self._to_jsonable(request_kwargs),
                "response": self._to_jsonable(response_obj),
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False))
                f.write("\n")
        except Exception:
            logger.exception("Failed to write raw model API response log")

    async def _call_llm(self, messages):
        """Intercept raw model API responses from SkillKit -> LLM_BASE_URL."""
        client = getattr(self, "client", None)
        completions = (
            getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None)
            if client is not None
            else None
        )
        if completions is None:
            return await super()._call_llm(messages)

        effective_model = getattr(self.config, "model", None)

        async def _wrapped_create(*args, **kwargs):
            safe_kwargs = _sanitize_reasoning_kwargs_for_model(
                effective_model,
                dict(kwargs),
            )
            if _is_gemini_model(effective_model):
                safe_kwargs = _inject_gemini_thought_signatures_into_messages(
                    safe_kwargs,
                    self._gemini_tool_call_signatures,
                )
            raw_response = await original_create(*args, **safe_kwargs)
            if _is_gemini_model(effective_model):
                self._gemini_tool_call_signatures.update(
                    _extract_gemini_thought_signatures_from_response(raw_response)
                )
            self._append_model_api_raw_log(
                request_kwargs=safe_kwargs,
                response_obj=raw_response,
            )
            return raw_response

        original_create = completions
        self.client.chat.completions.create = _wrapped_create
        try:
            return await super()._call_llm(messages)
        finally:
            self.client.chat.completions.create = original_create

    async def _execute_tool_base(self, tool_call, on_output=None) -> str:
        return await super()._execute_tool(tool_call, on_output)

    async def _execute_tool(self, tool_call, on_output=None):
        return await self._execute_tool_with_policy(tool_call, on_output=on_output)

    def _preprocess_validated_tool_call(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        tool_call: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str | None, str | None]:
        if tool_name != "execute":
            return super()._preprocess_validated_tool_call(
                tool_name=tool_name,
                args=args,
                tool_call=tool_call,
            )

        command = str(args.get("command") or "").strip()
        if not command or not _GRADLE_ANY_CMD_RE.search(command):
            return super()._preprocess_validated_tool_call(
                tool_name=tool_name,
                args=args,
                tool_call=tool_call,
            )

        force_system_gradle = _env_flag("SANDBOX_FORCE_SYSTEM_GRADLE", True)
        allow_wrapper_fallback = _env_flag("SANDBOX_ALLOW_WRAPPER_FALLBACK", True)
        timeout_seconds = _env_int("SANDBOX_GRADLE_CMD_TIMEOUT_SECONDS", 480)
        rewritten = command
        rewritten_flag = False
        strategy = "wrapper"
        if force_system_gradle:
            rewritten, rewritten_flag = _rewrite_gradle_wrapper_command(command)
            if rewritten_flag:
                rewritten, strategy = _build_gradle_command(
                    original_command=command,
                    rewritten_command=rewritten,
                    timeout_seconds=timeout_seconds,
                    allow_wrapper_fallback=allow_wrapper_fallback,
                )
            elif timeout_seconds > 0:
                strategy = "system"
                rewritten = f"timeout {timeout_seconds}s bash -lc {shlex.quote(rewritten)}"
        elif timeout_seconds > 0:
            rewritten = f"timeout {timeout_seconds}s bash -lc {shlex.quote(rewritten)}"

        if rewritten != command:
            logger.info(
                "gradle_command_rewrite strategy=%s rewritten=%s original_command=%s rewritten_command=%s",
                strategy,
                str(rewritten_flag).lower(),
                command,
                rewritten,
            )

        updated_args = dict(args)
        updated_args["command"] = rewritten
        normalized_tool_call = dict(tool_call)
        normalized_tool_call["arguments"] = json.dumps(updated_args, ensure_ascii=False)
        return normalized_tool_call, updated_args, None, None

    def _on_tool_validation_error(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        error_msg: str,
        started_at: float,
    ) -> str:
        self._append_tool_call_log(
            tool_name=tool_name,
            args=args,
            started_at=started_at,
            result_preview=error_msg,
            status="failed",
        )
        return error_msg

    def _on_tool_disallowed(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        error_msg: str,
        started_at: float,
    ) -> str:
        self._append_tool_call_log(
            tool_name=tool_name,
            args=args,
            started_at=started_at,
            result_preview=error_msg,
            status="failed",
        )
        return error_msg

    def _on_tool_result(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        started_at: float,
    ) -> str:
        self._append_tool_call_log(
            tool_name=tool_name,
            args=args,
            started_at=started_at,
            result_preview=str(result) if result else "",
            status=(
                "failed"
                if str(result).startswith(("Error:", "Exit code:"))
                else "success"
            ),
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
    requested_tools = set(body.get("allowed_tools", list(_ALL_TOOLS)))
    allowed_tools, unknown_tools = sanitize_requested_tools(requested_tools, _ALL_TOOLS)
    if unknown_tools:
        logger.warning("Ignoring unknown allowed_tools entries: %s", unknown_tools)
    if not allowed_tools:
        allowed_tools = set(_ALL_TOOLS)
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
    await _run_runtime_preflight_once()
    _configure_java_runtime_for_workspace(parsed["workdir"])

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
    await _run_runtime_preflight_once()
    _configure_java_runtime_for_workspace(parsed["workdir"])

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
