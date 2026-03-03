"""Abstract sandbox backend protocol.

Defines the interface that both Docker and BoxLite backends implement,
allowing ``SandboxManager`` to switch between them via configuration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable


@dataclass
class SandboxInfo:
    """Runtime info for a created sandbox.

    Backend-specific fields are stored in ``extra``.  The top-level fields
    are the contract that ``SandboxManager`` and ``executor.py`` rely on.
    """

    task_id: str
    sandbox_name: str  # human-readable identifier (container name or box id)
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
