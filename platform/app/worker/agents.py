"""Agent pool: create and cache SandboxedAgentRunner instances per (role, task_id)."""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

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


def _get_skill_dirs(role: str) -> list[Path]:
    """Return skill directories for a given role."""
    dir_names = _ROLE_SKILL_DIRS.get(role, ["shared"])
    dirs = []
    for name in dir_names:
        d = _SKILLS_ROOT / name
        if d.is_dir():
            dirs.append(d)
    return dirs


# Base class for SandboxedAgentRunner — use AgentRunner when available, object otherwise
_BaseRunner = AgentRunner if SKILLKIT_AVAILABLE else object


class SandboxedAgentRunner(_BaseRunner):  # type: ignore[misc]
    """AgentRunner with per-task working directory and role-based tool filtering."""

    def __init__(self, *args, default_cwd: str | None = None,
                 allowed_tools: set[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_cwd = default_cwd
        self.allowed_tools = allowed_tools or _ALL_TOOLS

    def get_tools(self):
        tools = super().get_tools()
        tools = [t for t in tools
                 if t["function"]["name"] in self.allowed_tools]
        return tools

    async def _execute_tool(self, tool_call, on_output=None):
        name = tool_call.get("name", "")
        # Inject default cwd for execution tools
        if self.default_cwd and name in ("execute", "execute_script"):
            args = json.loads(tool_call.get("arguments", "{}"))
            if not args.get("cwd"):
                args["cwd"] = self.default_cwd
                tool_call = {**tool_call, "arguments": json.dumps(args)}
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

    max_turns = _MAX_TURNS.get(role, _DEFAULT_MAX_TURNS)
    allowed = ROLE_TOOLS.get(role, _ALL_TOOLS) if enable_tools else set()

    skill_dirs = _get_skill_dirs(role)

    create_kwargs: dict = dict(
        skill_dirs=skill_dirs,
        system_prompt=system_prompt,
        max_turns=max_turns,
        enable_tools=enable_tools,
        load_context_files=False,
    )
    if model:
        create_kwargs["model"] = model

    base = AgentRunner.create(**create_kwargs)
    runner = SandboxedAgentRunner(
        engine=base.engine,
        config=base.config,
        default_cwd=str(workdir),
        allowed_tools=allowed,
    )
    logger.info(
        "Created SandboxedAgentRunner for role=%s task=%s model=%s tools=%s enable_tools=%s cwd=%s",
        role, task_id, model or "default", sorted(allowed), enable_tools, workdir,
    )
    return runner


def get_agent(
    role: str, task_id: str, *, model: str | None = None,
) -> "SandboxedAgentRunner":
    """Return (or create) a SandboxedAgentRunner for the given (role, task_id)."""
    key = f"{role}:{task_id}"
    if key not in _agents:
        resolved_model = resolve_model_for_role(role, model)
        _agents[key] = _create_runner(role, task_id, enable_tools=True, model=resolved_model)
    return _agents[key]


def get_agent_text_only(
    role: str, task_id: str, *, model: str | None = None,
) -> "SandboxedAgentRunner":
    """Return a text-only AgentRunner (no tools) for fallback when tool calling fails.

    Used when the LLM model (e.g. MiniMax) doesn't support reliable tool calling.
    Creates a separate cached instance with a ':textonly' suffix.
    """
    key = f"{role}:{task_id}:textonly"
    if key not in _agents:
        resolved_model = resolve_model_for_role(role, model)
        _agents[key] = _create_runner(role, task_id, enable_tools=False, model=resolved_model)
    return _agents[key]


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
