"""Agent pool: create and cache SandboxedAgentRunner instances per (role, task_id)."""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.worker.prompts import SYSTEM_PROMPTS
from sandbox.tool_policy import (
    DEFAULT_FALLBACK_CORE_TOOLS,
    DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS,
    ToolExecutionPolicyMixin,
    discover_tool_catalog,
)

try:
    from skillkit import AgentRunner
    SKILLKIT_AVAILABLE = True
except ImportError:
    AgentRunner = None  # type: ignore[assignment,misc]
    SKILLKIT_AVAILABLE = False

logger = logging.getLogger(__name__)

_agents: dict[str, AgentRunner] = {}

# Roles that need more turns for deep exploration / code generation
_MAX_TURNS: dict[str, int] = {
    "spec": 5,
    "coding": 8,
    "doc": 5,
    "test": 8,
    "verify": 3,
}
_DEFAULT_MAX_TURNS = 5

# Per-role tool whitelist (SkillKit built-in: read, write, edit, execute, execute_script, skill)
ROLE_TOOLS: dict[str, set[str]] = {
    "orchestrator": {"read", "execute", "skill"},
    "spec":         {"read", "write", "edit", "skill"},
    "coding":       {"read", "write", "edit", "execute", "execute_script", "skill"},
    "test":         {"read", "write", "edit", "execute", "execute_script", "skill"},
    "review":       {"read", "execute", "skill"},
    "smoke":        {"read", "execute", "skill"},
    "doc":          {"read", "write", "edit", "skill"},
    "verify":       {"execute", "read"},
}
_ALL_TOOLS: set[str] = set()
_TOOL_ARGUMENT_HINTS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Skills configuration: each role loads shared + role-specific skill dirs
# ---------------------------------------------------------------------------
_SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent / "skills"

_ROLE_SKILL_DIRS: dict[str, list[str]] = {
    "orchestrator": ["shared", "orchestrator"],
    "spec":         ["shared", "spec"],
    "coding":       ["shared", "coding"],
    "test":         ["shared", "test"],
    "review":       ["shared", "review"],
    "smoke":        ["shared", "smoke"],
    "doc":          ["shared", "doc"],
    "verify":       ["shared"],
}


def _get_skill_dirs(role: str, extra_skill_dirs: list[str] | None = None) -> list[Path]:
    """Return skill directories for a given role."""
    dir_names = _ROLE_SKILL_DIRS.get(role, ["shared"])
    dirs = []
    for name in dir_names:
        d = _SKILLS_ROOT / name
        if d.is_dir():
            dirs.append(d.resolve())
    for extra in extra_skill_dirs or []:
        candidate = Path(extra).expanduser().resolve()
        if candidate.is_dir():
            dirs.append(candidate)
        else:
            logger.warning("Skip non-existing extra skill dir: %s", candidate)
    deduped: list[Path] = []
    seen: set[str] = set()
    for item in dirs:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _create_tool_probe_runner():
    if not SKILLKIT_AVAILABLE:
        raise RuntimeError("SkillKit unavailable")
    return AgentRunner.create(
        skill_dirs=_get_skill_dirs("coding"),
        system_prompt=SYSTEM_PROMPTS.get("coding", ""),
        enable_tools=True,
        load_context_files=False,
        max_turns=1,
    )


def _refresh_tool_catalog() -> None:
    """Refresh dynamic tool catalog and argument hints from SkillKit source of truth."""
    global _ALL_TOOLS, _TOOL_ARGUMENT_HINTS
    _ALL_TOOLS, _TOOL_ARGUMENT_HINTS = discover_tool_catalog(
        create_probe_runner=_create_tool_probe_runner,
        fallback_core_tools=DEFAULT_FALLBACK_CORE_TOOLS,
        fallback_hints=DEFAULT_FALLBACK_TOOL_ARGUMENT_HINTS,
        logger=logger,
        warning_message="Failed to discover tool schemas from SkillKit; fallback to defaults",
    )


def get_all_tools() -> set[str]:
    return set(_ALL_TOOLS)


def validate_role_tools_or_raise(*, fail_on_unknown: bool = True) -> None:
    unknown_by_role: dict[str, list[str]] = {}
    for role, tools in ROLE_TOOLS.items():
        unknown = sorted(tool for tool in tools if tool not in _ALL_TOOLS)
        if unknown:
            unknown_by_role[role] = unknown
    if not unknown_by_role:
        return
    message = (
        "ROLE_TOOLS contains unknown tool names. "
        f"known={sorted(_ALL_TOOLS)} unknown_by_role={unknown_by_role}"
    )
    if fail_on_unknown:
        raise RuntimeError(message)
    logger.warning(message)


_refresh_tool_catalog()
validate_role_tools_or_raise(fail_on_unknown=True)


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


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _extract_gemini_thought_signatures_from_response(response_obj: Any) -> dict[str, str]:
    """Extract OpenAI-compat thought signatures keyed by tool_call id."""
    data = _to_jsonable(response_obj)
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


def _build_runtime_signature(
    *,
    model: str | None,
    temperature: float | None,
    max_tokens: int | None,
    max_turns: int | None,
    skill_dirs: list[Path],
    system_prompt_append: str | None,
) -> tuple:
    return (
        model or "",
        temperature,
        max_tokens,
        max_turns,
        tuple(str(p) for p in skill_dirs),
        (system_prompt_append or "").strip(),
    )


def _resolve_max_turns(role: str, override: int | None) -> int:
    if isinstance(override, int) and override > 0:
        return override
    return _MAX_TURNS.get(role, _DEFAULT_MAX_TURNS)


def _normalize_prompt_append(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _runner_signature(runner: "SandboxedAgentRunner") -> tuple | None:
    return getattr(runner, "_runtime_signature", None)


def _assign_runner_signature(runner: "SandboxedAgentRunner", signature: tuple) -> None:
    setattr(runner, "_runtime_signature", signature)


def _needs_runner_refresh(runner: "SandboxedAgentRunner", signature: tuple) -> bool:
    return _runner_signature(runner) != signature


def _create_or_refresh_runner(
    key: str,
    *,
    role: str,
    task_id: str,
    enable_tools: bool,
    model: str | None,
    temperature: float | None,
    max_tokens: int | None,
    max_turns: int | None,
    extra_skill_dirs: list[str] | None,
    system_prompt_append: str | None,
) -> "SandboxedAgentRunner":
    skill_dirs = _get_skill_dirs(role, extra_skill_dirs)
    effective_max_turns = _resolve_max_turns(role, max_turns)
    normalized_prompt_append = _normalize_prompt_append(system_prompt_append)
    signature = _build_runtime_signature(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_turns=effective_max_turns,
        skill_dirs=skill_dirs,
        system_prompt_append=normalized_prompt_append,
    )
    existing = _agents.get(key)
    if existing is not None and not _needs_runner_refresh(existing, signature):
        return existing

    if existing is not None:
        logger.info(
            "Refreshing cached runner for key=%s due to runtime config change",
            key,
        )

    created = _create_runner(
        role,
        task_id,
        enable_tools=enable_tools,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_turns=effective_max_turns,
        skill_dirs=skill_dirs,
        system_prompt_append=normalized_prompt_append,
    )
    _assign_runner_signature(created, signature)
    _agents[key] = created
    return created


# Base class for SandboxedAgentRunner — use AgentRunner when available, object otherwise
_BaseRunner = AgentRunner if SKILLKIT_AVAILABLE else object


class SandboxedAgentRunner(ToolExecutionPolicyMixin, _BaseRunner):  # type: ignore[misc]
    """AgentRunner with per-task working directory and role-based tool filtering."""

    def __init__(self, *args, default_cwd: str | None = None,
                 allowed_tools: set[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_cwd = default_cwd
        self.allowed_tools = allowed_tools or _ALL_TOOLS
        self._tool_argument_hints = _TOOL_ARGUMENT_HINTS
        self._gemini_tool_call_signatures: dict[str, str] = {}

    def _resolve_workspace_path(self, path: str) -> tuple[str, str | None]:
        """Resolve a possibly-relative path into task workspace safely."""
        if not self.default_cwd or not path:
            return path, None

        raw = Path(path)
        if raw.is_absolute():
            return str(raw), None

        workspace = Path(self.default_cwd).resolve()
        candidate = (workspace / raw).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError:
            return path, f"Error: Path escapes workspace: {path}"
        return str(candidate), None

    def _format_directory_listing(self, path: Path, original: str) -> str:
        """Return deterministic listing so model can probe workspace via read."""
        entries = sorted(path.iterdir(), key=lambda p: p.name)
        max_items = 200
        lines = [f"Directory listing for {original}:"]
        for item in entries[:max_items]:
            suffix = "/" if item.is_dir() else ""
            lines.append(f"- {item.name}{suffix}")
        if len(entries) > max_items:
            lines.append(f"... ({len(entries) - max_items} more entries)")
        return "\n".join(lines)

    def get_tools(self):
        tools = super().get_tools()
        tools = [t for t in tools
                 if t["function"]["name"] in self.allowed_tools]
        return tools

    async def _call_llm(self, messages):
        """Keep reasoning enabled while stripping provider-incompatible payload fields."""
        client = getattr(self, "client", None)
        completions = (
            getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None)
            if client is not None
            else None
        )
        if completions is None:
            return await super()._call_llm(messages)

        effective_model = getattr(self.config, "model", None)
        original_create = completions

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
            response = await original_create(*args, **safe_kwargs)
            if _is_gemini_model(effective_model):
                self._gemini_tool_call_signatures.update(
                    _extract_gemini_thought_signatures_from_response(response)
                )
            return response

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
        if not self.default_cwd or tool_name not in ("read", "write", "edit"):
            return tool_call, args, None, None

        path = str(args.get("path") or "")
        resolved_path, error = self._resolve_workspace_path(path)
        if error:
            return tool_call, args, error, None

        if resolved_path != path:
            args = dict(args)
            args["path"] = resolved_path
            tool_call = {**tool_call, "arguments": json.dumps(args, ensure_ascii=False)}

        # Doc role has no `execute`; support directory probing via `read`.
        if tool_name == "read" and resolved_path:
            target = Path(resolved_path)
            if target.exists() and target.is_dir():
                listing = self._format_directory_listing(target, path or resolved_path)
                return tool_call, args, None, listing

        return tool_call, args, None, None


def resolve_model_for_role(role: str, stage_model: str | None = None) -> str | None:
    """Resolve which LLM model to use for a given role.

    Priority: stage-level override > role-model map > global LLM_MODEL (None = use default).
    Returns None when the global default should be used (avoids passing redundant config).
    """
    if stage_model:
        return stage_model
    try:
        role_map = json.loads(settings.LLM_ROLE_MODEL_MAP)
    except (json.JSONDecodeError, TypeError):
        return None
    return role_map.get(role)


def _create_runner(
    role: str, task_id: str, *, enable_tools: bool = True,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_turns: int | None = None,
    skill_dirs: list[Path] | None = None,
    system_prompt_append: str | None = None,
) -> "SandboxedAgentRunner":
    """Internal helper to create a SandboxedAgentRunner."""
    if not SKILLKIT_AVAILABLE:
        raise RuntimeError(
            "SkillKit is not installed. Install it via 'pip install skillkit' "
            "or set WORKER_ENABLED=false to run without the agent worker."
        )

    workdir = Path(tempfile.gettempdir()) / "silicon_agent" / "tasks" / task_id
    workdir.mkdir(parents=True, exist_ok=True)

    system_prompt = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPTS["orchestrator"])
    system_prompt += f"\n\n你的工作目录是: {workdir}\n所有文件操作请在此目录下进行。"

    effective_max_turns = _resolve_max_turns(role, max_turns)
    allowed = ROLE_TOOLS.get(role, _ALL_TOOLS) if enable_tools else set()

    effective_skill_dirs = skill_dirs or _get_skill_dirs(role)
    normalized_prompt_append = _normalize_prompt_append(system_prompt_append)
    if normalized_prompt_append:
        system_prompt += f"\n\n{normalized_prompt_append}"

    create_kwargs: dict = dict(
        skill_dirs=effective_skill_dirs,
        system_prompt=system_prompt,
        max_turns=effective_max_turns,
        enable_tools=enable_tools,
        load_context_files=False,
    )

    base = AgentRunner.create(**create_kwargs)
    if model:
        # SkillKit's AgentConfig.from_env already sets model internally.
        # Override after creation to avoid duplicate keyword collisions.
        base.config.model = model
    if temperature is not None:
        # Keep runtime override handling consistent with model override.
        base.config.temperature = temperature
    if max_tokens is not None:
        # Keep runtime override handling consistent with model override.
        base.config.max_tokens = max_tokens

    runner = SandboxedAgentRunner(
        engine=base.engine,
        config=base.config,
        default_cwd=str(workdir),
        allowed_tools=allowed,
    )
    configured_model = getattr(runner.config, "model", None)
    configured_temperature = getattr(runner.config, "temperature", None)
    configured_max_tokens = getattr(runner.config, "max_tokens", None)
    logger.info(
        "Created SandboxedAgentRunner for role=%s task=%s requested_model=%s "
        "effective_model=%s temperature=%s max_tokens=%s max_turns=%s "
        "skill_dirs=%s tools=%s enable_tools=%s cwd=%s",
        role,
        task_id,
        model or "default",
        configured_model or "default",
        configured_temperature,
        configured_max_tokens,
        effective_max_turns,
        [str(p) for p in effective_skill_dirs],
        sorted(allowed),
        enable_tools,
        workdir,
    )
    return runner


def get_agent(
    role: str, task_id: str, *, model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_turns: int | None = None,
    extra_skill_dirs: list[str] | None = None,
    system_prompt_append: str | None = None,
) -> "SandboxedAgentRunner":
    """Return (or create) a SandboxedAgentRunner for the given (role, task_id)."""
    key = f"{role}:{task_id}"
    resolved_model = resolve_model_for_role(role, model)
    return _create_or_refresh_runner(
        key,
        role=role,
        task_id=task_id,
        enable_tools=True,
        model=resolved_model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_turns=max_turns,
        extra_skill_dirs=extra_skill_dirs,
        system_prompt_append=system_prompt_append,
    )


def get_agent_text_only(
    role: str, task_id: str, *, model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_turns: int | None = None,
    extra_skill_dirs: list[str] | None = None,
    system_prompt_append: str | None = None,
) -> "SandboxedAgentRunner":
    """Return a text-only AgentRunner (no tools) for fallback when tool calling fails.

    Used when the LLM model (e.g. MiniMax) doesn't support reliable tool calling.
    Creates a separate cached instance with a ':textonly' suffix.
    """
    key = f"{role}:{task_id}:textonly"
    resolved_model = resolve_model_for_role(role, model)
    return _create_or_refresh_runner(
        key,
        role=role,
        task_id=task_id,
        enable_tools=False,
        model=resolved_model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_turns=max_turns,
        extra_skill_dirs=extra_skill_dirs,
        system_prompt_append=system_prompt_append,
    )


def close_agents_for_task(task_id: str) -> None:
    """Discard all cached AgentRunner instances for a specific task."""
    keys_to_remove = [k for k in _agents if f":{task_id}" in k]
    for k in keys_to_remove:
        del _agents[k]
    if keys_to_remove:
        logger.info("Closed %d agent(s) for task %s", len(keys_to_remove), task_id)


def close_all_agents() -> None:
    """Discard all cached AgentRunner instances."""
    count = len(_agents)
    _agents.clear()
    if count:
        logger.info("Closed %d AgentRunner instances", count)
