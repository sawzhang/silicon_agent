"""Abstract sandbox backend protocol.

Defines the interface that both Docker and BoxLite backends implement,
allowing ``SandboxManager`` to switch between them via configuration.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class SandboxInfo:
    """Runtime info for a created sandbox.

    Backend-specific fields are stored in ``extra``.  The top-level fields
    are the contract that ``SandboxManager`` and ``executor.py`` rely on.
    """

    task_id: str
    sandbox_name: str  # human-readable identifier (container name or box id)
    role: str | None = None  # agent role this sandbox serves (e.g. "coding", "test")
    created_at: float = field(default_factory=time.monotonic)
    extra: dict[str, Any] = field(default_factory=dict)

    # --- Docker-specific convenience accessors (kept for backward compat) ---

    @property
    def container_id(self) -> str:
        return str(self.extra.get("container_id", ""))

    @property
    def container_name(self) -> str:
        return self.sandbox_name

    @property
    def host(self) -> str:
        return str(self.extra.get("host", ""))

    @property
    def port(self) -> int:
        return int(self.extra.get("port", 0))


@dataclass
class RoleResourceProfile:
    """Resource profile for a specific agent role's sandbox."""

    cpus: int = 2
    memory_mib: int = 4096
    image: str | None = None  # None → use SANDBOX_IMAGE
    mount_mode: str = "rw"  # "rw" or "ro"


def get_role_resource_profile(role: str) -> RoleResourceProfile:
    """Parse SANDBOX_ROLE_RESOURCES for a given role, fall back to defaults.

    The config is a JSON object mapping role names to resource overrides::

        {"test": {"cpus": 4, "memory_mib": 4096}, "review": {"mount_mode": "ro"}}

    Missing keys within a role entry fall back to ``RoleResourceProfile`` defaults.
    Roles not present in the mapping return a plain default profile.
    """
    from app.config import settings  # noqa: PLC0415

    raw = getattr(settings, "SANDBOX_ROLE_RESOURCES", "{}")
    try:
        mapping = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid SANDBOX_ROLE_RESOURCES JSON, using defaults")
        mapping = {}

    if not isinstance(mapping, dict):
        return RoleResourceProfile()

    role_cfg = mapping.get(role)
    if role_cfg is None:
        return RoleResourceProfile()
    if not isinstance(role_cfg, dict):
        return RoleResourceProfile()

    return RoleResourceProfile(
        cpus=int(role_cfg.get("cpus", 2)),
        memory_mib=int(role_cfg.get("memory_mib", 4096)),
        image=role_cfg.get("image"),
        mount_mode=str(role_cfg.get("mount_mode", "rw")),
    )


@dataclass
class SandboxCreateResult:
    """Result for sandbox create operation."""

    info: Optional[SandboxInfo] = None
    workspace: str = ""
    workspace_source: str = "fallback"
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class SandboxResult:
    """Result from executing a stage inside a sandbox."""

    text_content: str = ""
    total_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    streamed: bool = False


# Callback type for streaming sandbox events (NDJSON-style dicts)
OnSandboxEvent = Callable[[dict[str, Any]], Awaitable[None] | None]


@runtime_checkable
class SandboxBackend(Protocol):
    """Protocol that all sandbox backends must satisfy."""

    async def create(
        self,
        task_id: str,
        *,
        workspace: str,
        workspace_source: str = "fallback",
        image: Optional[str] = None,
        role: Optional[str] = None,
        cpus: Optional[int] = None,
        memory_mib: Optional[int] = None,
        mount_mode: str = "rw",
    ) -> SandboxCreateResult:
        """Create a sandbox for a task, mounting ``workspace``."""
        ...

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
        """Execute a stage inside the sandbox, streaming events via *on_event*."""
        ...

    async def destroy(self, task_id: str) -> None:
        """Destroy a task's sandbox and release resources."""
        ...

    async def destroy_all(self) -> None:
        """Destroy all managed sandboxes."""
        ...

    def get_info(self, task_id: str) -> Optional[SandboxInfo]:
        """Return the sandbox info for a task, or ``None``."""
        ...
