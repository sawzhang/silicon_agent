"""Agent pool: create and cache SandboxedAgentRunner instances per (role, task_id)."""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.worker.prompts import SYSTEM_PROMPTS

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
    "spec": 20,
    "coding": 20,
    "doc": 20,
    "test": 20,
}
_DEFAULT_MAX_TURNS = 10

# Per-role tool whitelist (SkillKit built-in: read, write, execute, execute_script, skill)
ROLE_TOOLS: dict[str, set[str]] = {
    "orchestrator": {"read", "execute", "skill"},
    "spec":         {"read", "write", "skill"},
    "coding":       {"read", "write", "execute", "execute_script", "skill"},
    "test":         {"read", "write", "execute", "execute_script", "skill"},
    "review":       {"read", "execute", "skill"},
    "smoke":        {"read", "execute", "skill"},
    "doc":          {"read", "write", "skill"},
}
_ALL_TOOLS = {"read", "write", "execute", "execute_script", "skill"}

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


class SandboxedAgentRunner(_BaseRunner):  # type: ignore[misc]
    """AgentRunner with per-task working directory and role-based tool filtering."""

    def __init__(self, *args, default_cwd: str | None = None,
                 allowed_tools: set[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_cwd = default_cwd
        self.allowed_tools = allowed_tools or _ALL_TOOLS

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

    async def _execute_tool(self, tool_call, on_output=None):
        name = tool_call.get("name", "")
        args: dict[str, Any] = {}
        raw_args = tool_call.get("arguments", "{}")
        try:
            if isinstance(raw_args, dict):
                parsed_args: Any = raw_args
            elif isinstance(raw_args, str):
                parsed_args = json.loads(raw_args or "{}")
            else:
                parsed_args = {}
            if not isinstance(parsed_args, dict):
                raise TypeError(
                    f"Invalid arguments for tool {name}: expected JSON object, got {type(parsed_args).__name__}"
                )
            args = parsed_args
        except Exception as exc:
            return f"Error: Invalid arguments for tool {name}: {exc}"

        # Inject default cwd for execution tools
        if self.default_cwd and name in ("execute", "execute_script"):
            if not args.get("cwd"):
                args["cwd"] = self.default_cwd
        tool_call = {**tool_call, "arguments": json.dumps(args, ensure_ascii=False)}

        # Resolve read/write relative paths into task workspace.
        if self.default_cwd and name in ("read", "write"):
            path = str(args.get("path") or "")
            resolved_path, error = self._resolve_workspace_path(path)
            if error:
                return error
            if resolved_path != path:
                args["path"] = resolved_path
                tool_call = {**tool_call, "arguments": json.dumps(args)}

            # Doc role has no `execute`; support directory probing via `read`.
            if name == "read":
                target = Path(resolved_path)
                if target.exists() and target.is_dir():
                    return self._format_directory_listing(target, path or resolved_path)

        # Block tools not in whitelist (belt-and-suspenders)
        if name not in self.allowed_tools:
            return f"Error: {name} is not allowed for this role"
        return await super()._execute_tool(tool_call, on_output)


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

    # Disable MiniMax-specific reasoning_split for non-MiniMax models (e.g. Gemini).
    # SkillKit defaults enable_reasoning=True which injects extra_body={"reasoning_split": True}
    # into every API call — Gemini returns HTTP 400 for this unrecognized field.
    effective_model = (model or getattr(base.config, "model", "") or "").lower()
    if "minimax" not in effective_model:
        base.config.enable_reasoning = False
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
