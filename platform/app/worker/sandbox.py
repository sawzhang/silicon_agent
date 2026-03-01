"""Docker container sandbox lifecycle management.

Each task gets an isolated Docker container running the full agent stack
(LLM client + SkillKit tools). The platform communicates with the container
via HTTP to the embedded agent server.

Architecture (方式1 — 整体容器化):
    Platform ──HTTP──> Container (agent_server.py)
                         ├─ LLM client → OpenAI/etc API
                         ├─ read/write tools → /workspace (bind mount)
                         └─ execute/execute_script → container shell
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from app.config import settings
from app.integration.skillkit_env import build_sandbox_llm_env
from app.worker.agents import get_all_tools

logger = logging.getLogger(__name__)
_MODEL_API_LOG_MOUNT_DIR = "/model_api_logs"

# Semaphore to limit concurrent containers
_concurrency_sem: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _concurrency_sem
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(settings.SANDBOX_MAX_CONCURRENT)
    return _concurrency_sem


async def _run(cmd: list[str], timeout: float = 60) -> tuple[int, str, str]:
    """Run a command asynchronously without shell interpolation."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 1, "", f"Command timed out after {timeout}s"
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


@dataclass
class SandboxInfo:
    """Runtime info for a created sandbox container."""
    container_id: str
    container_name: str
    host: str
    port: int
    task_id: str
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class SandboxCreateResult:
    """Result for sandbox container create operation."""

    info: Optional[SandboxInfo] = None
    workspace: str = ""
    workspace_source: str = "fallback"
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class SandboxResult:
    """Result from executing a stage inside a sandbox container."""
    text_content: str = ""
    total_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    streamed: bool = False


class SandboxManager:
    """Manages Docker container lifecycle for sandboxed agent execution."""

    def __init__(self):
        self._containers: Dict[str, SandboxInfo] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(600, connect=10),
                transport=httpx.AsyncHTTPTransport(proxy=None),
            )
        return self._http_client

    async def create(
        self,
        task_id: str,
        *,
        workspace: str,
        workspace_source: str = "fallback",
        image: Optional[str] = None,
    ) -> SandboxCreateResult:
        """Create a sandbox container for a task.

        Mounts workspace at /workspace inside the container.
        The container runs agent_server.py which listens on port 9090.
        """
        def _failed(
            *,
            error_code: str,
            error_message: str,
            release_sem: bool = True,
        ) -> SandboxCreateResult:
            if release_sem:
                sem.release()
            logger.error("Sandbox create failed (%s): %s", error_code, error_message)
            return SandboxCreateResult(
                info=None,
                workspace=workspace,
                workspace_source=workspace_source,
                error_code=error_code,
                error_message=error_message,
            )

        sem = _get_semaphore()
        acquired = sem._value > 0  # Check without blocking
        if not acquired:
            logger.warning(
                "Max concurrent sandboxes (%d) reached, waiting...",
                settings.SANDBOX_MAX_CONCURRENT,
            )
        await sem.acquire()

        container_name = f"sa-sandbox-{task_id[:12]}"
        resolved_image = image or settings.SANDBOX_IMAGE
        workspace_path = Path(workspace)
        if not workspace_path.exists() or not workspace_path.is_dir():
            return _failed(
                error_code="workspace_not_found",
                error_message=f"Sandbox workspace path does not exist or is not a directory: {workspace}",
            )

        # Build docker run command
        docker_cmd = self._build_docker_run_cmd(
            container_name=container_name,
            image=resolved_image,
            workspace=workspace,
            task_id=task_id,
        )

        logger.info("Creating sandbox container: %s (image=%s)", container_name, resolved_image)
        rc, out, err = await _run(docker_cmd, timeout=120)

        if rc != 0:
            return _failed(
                error_code="docker_run_failed",
                error_message=err or "docker run failed",
            )

        container_id = out.strip()

        # Wait for agent server to be ready
        host = await self._resolve_container_host(container_name)
        if not host:
            sem.release()
            await self._force_remove(container_name)
            return SandboxCreateResult(
                info=None,
                workspace=workspace,
                workspace_source=workspace_source,
                error_code="container_host_unresolved",
                error_message=f"Could not resolve container host for {container_name}",
            )

        port = settings.SANDBOX_AGENT_PORT
        info = SandboxInfo(
            container_id=container_id,
            container_name=container_name,
            host=host,
            port=port,
            task_id=task_id,
        )

        if not await self._wait_for_healthy(info, timeout=60):
            sem.release()
            logger.error("Sandbox container %s failed health check", container_name)
            await self._force_remove(container_name)
            return SandboxCreateResult(
                info=None,
                workspace=workspace,
                workspace_source=workspace_source,
                error_code="container_unhealthy",
                error_message=f"Sandbox container {container_name} failed health check",
            )

        self._containers[task_id] = info
        logger.info(
            "Sandbox container ready: %s (%s:%d)",
            container_name, host, port,
        )
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
        on_event: Optional[Callable[[dict[str, Any]], Awaitable[None] | None]] = None,
    ) -> SandboxResult:
        """Execute a stage in the sandbox container via HTTP.

        Prefer streaming endpoint (/execute_stream). When unavailable, fall back
        to legacy endpoint (/execute).
        """
        payload = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_turns": max_turns,
            "enable_tools": enable_tools,
            "allowed_tools": allowed_tools or sorted(get_all_tools()),
            "skill_dirs": skill_dirs or ["/skills/shared"],
            "workdir": workdir,
            "timeout": timeout,
        }

        logger.info(
            "Sending stage to sandbox %s (model=%s, temperature=%s, max_tokens=%s, timeout=%ds)",
            info.container_name,
            model or "default",
            temperature,
            max_tokens,
            timeout,
        )

        request_timeout = httpx.Timeout(timeout + 30, connect=10)
        stream_url = f"http://{info.host}:{info.port}/execute_stream"

        try:
            async with self.http_client.stream(
                "POST",
                stream_url,
                json=payload,
                timeout=request_timeout,
            ) as resp:
                if resp.status_code == 404:
                    logger.info(
                        "Sandbox stream endpoint is unavailable on %s, fallback to /execute",
                        info.container_name,
                    )
                    return await self._execute_stage_legacy(
                        info,
                        payload=payload,
                        timeout=request_timeout,
                    )

                resp.raise_for_status()
                final_payload: Optional[dict[str, Any]] = None
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Ignored malformed sandbox stream payload from %s: %s",
                            info.container_name,
                            line[:200],
                        )
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    event_type = str(parsed.get("type") or "")
                    event_data = parsed.get("data")
                    normalized_data = event_data if isinstance(event_data, dict) else {}
                    if event_type == "final":
                        final_payload = normalized_data
                        break
                    if on_event is not None:
                        callback_result = on_event(
                            {"type": event_type, "data": normalized_data}
                        )
                        if inspect.isawaitable(callback_result):
                            await callback_result

                if final_payload is None:
                    return SandboxResult(
                        error=(
                            f"Sandbox stream ended without final event: {info.container_name}"
                        ),
                        streamed=True,
                    )

                return SandboxResult(
                    text_content=str(final_payload.get("text_content", "")),
                    total_tokens=int(final_payload.get("total_tokens", 0) or 0),
                    tool_calls=(
                        final_payload.get("tool_calls", [])
                        if isinstance(final_payload.get("tool_calls"), list)
                        else []
                    ),
                    error=(
                        str(final_payload.get("error"))
                        if final_payload.get("error") is not None
                        else None
                    ),
                    streamed=True,
                )
        except httpx.HTTPStatusError as e:
            return SandboxResult(
                error=(
                    f"Sandbox HTTP {e.response.status_code} from "
                    f"{info.container_name}: {e.response.text[:500]}"
                ),
                streamed=True,
            )
        except httpx.TimeoutException:
            return SandboxResult(error=f"HTTP timeout after {timeout}s to sandbox {info.container_name}")
        except httpx.ConnectError as e:
            return SandboxResult(error=f"Cannot connect to sandbox {info.container_name}: {e}")
        except Exception as e:
            return SandboxResult(error=f"Sandbox execution error: {e}")

    async def _execute_stage_legacy(
        self,
        info: SandboxInfo,
        *,
        payload: dict[str, Any],
        timeout: httpx.Timeout,
    ) -> SandboxResult:
        """Execute stage against legacy non-streaming endpoint (/execute)."""
        url = f"http://{info.host}:{info.port}/execute"
        resp = await self.http_client.post(
            url,
            json=payload,
            timeout=timeout,
        )
        data = resp.json()
        return SandboxResult(
            text_content=data.get("text_content", ""),
            total_tokens=data.get("total_tokens", 0),
            tool_calls=data.get("tool_calls", []),
            error=data.get("error"),
            streamed=False,
        )

    async def destroy(self, task_id: str) -> None:
        """Destroy a task's sandbox container and release resources."""
        info = self._containers.pop(task_id, None)
        if info:
            await self._force_remove(info.container_name)
            _get_semaphore().release()
            logger.info("Destroyed sandbox container: %s", info.container_name)

    async def destroy_all(self) -> None:
        """Destroy all managed sandbox containers."""
        task_ids = list(self._containers.keys())
        for task_id in task_ids:
            await self.destroy(task_id)
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def get_info(self, task_id: str) -> Optional[SandboxInfo]:
        return self._containers.get(task_id)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _build_docker_run_cmd(
        self,
        container_name: str,
        image: str,
        workspace: str,
        task_id: str,
    ) -> list[str]:
        """Build the docker run command with security constraints."""
        parts = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            # Resource limits
            f"--cpus={settings.SANDBOX_CPUS}",
            f"--memory={settings.SANDBOX_MEMORY}",
            f"--memory-swap={settings.SANDBOX_MEMORY}",
            f"--pids-limit={settings.SANDBOX_PIDS_LIMIT}",
            "--ulimit",
            "nofile=1024:2048",
            # Security
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
            # Filesystem
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
        ]
        capture_model_api_raw = bool(settings.SANDBOX_DUMP_MODEL_API_RESPONSE)
        container_raw_log_path: str | None = None
        if capture_model_api_raw:
            host_log_dir = Path(settings.SANDBOX_MODEL_API_RAW_LOG_HOST_DIR).expanduser()
            try:
                host_log_dir.mkdir(parents=True, exist_ok=True)
                parts.extend(
                    [
                        "--mount",
                        f"type=bind,src={host_log_dir},dst={_MODEL_API_LOG_MOUNT_DIR}",
                    ]
                )
                container_raw_log_path = f"{_MODEL_API_LOG_MOUNT_DIR}/{task_id}.jsonl"
            except Exception:
                logger.warning(
                    "Failed to prepare model API raw log mount directory: %s",
                    host_log_dir,
                    exc_info=True,
                )
                capture_model_api_raw = False

        if settings.SANDBOX_READONLY_ROOT:
            parts.append("--read-only")
            parts.extend(["--tmpfs", "/tmp:size=512m"])
            parts.extend(["--tmpfs", "/home/agent:size=256m"])

        # Network — use custom sandbox network if it exists, else host
        parts.extend(["--network", settings.SANDBOX_NETWORK])

        # LLM environment (platform-native + SkillKit compatibility env keys)
        llm_env = build_sandbox_llm_env(
            llm_api_key=settings.LLM_API_KEY,
            llm_base_url=settings.LLM_BASE_URL,
            llm_model=settings.LLM_MODEL,
            agent_port=settings.SANDBOX_AGENT_PORT,
        )
        for key, value in llm_env.items():
            parts.extend(["-e", f"{key}={value}"])
        parts.extend(
            [
                "-e",
                f"SANDBOX_DUMP_MODEL_API_RESPONSE={'true' if capture_model_api_raw else 'false'}",
            ]
        )
        if capture_model_api_raw and container_raw_log_path:
            parts.extend(["-e", f"SANDBOX_MODEL_API_RAW_LOG_PATH={container_raw_log_path}"])

        parts.append(image)

        return parts

    async def _resolve_container_host(self, container_name: str) -> Optional[str]:
        """Resolve the container's IP address on the sandbox network."""
        network = settings.SANDBOX_NETWORK

        # Try Docker network inspect to get container IP
        rc, out, err = await _run(
            [
                "docker",
                "inspect",
                "-f",
                f"{{{{.NetworkSettings.Networks.{network}.IPAddress}}}}",
                container_name,
            ],
            timeout=10,
        )
        if rc == 0 and out.strip("' \n"):
            ip = out.strip("' \n")
            if ip:
                return ip

        # Fallback: try to get IP from any network
        rc, out, err = await _run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container_name,
            ],
            timeout=10,
        )
        if rc == 0 and out.strip("' \n"):
            ip = out.strip("' \n")
            if ip:
                return ip

        # Last resort: localhost (container on host network)
        logger.warning("Could not resolve container IP for %s, falling back to 127.0.0.1", container_name)
        return "127.0.0.1"

    async def _wait_for_healthy(self, info: SandboxInfo, timeout: float = 60) -> bool:
        """Poll the container's health endpoint until it responds."""
        url = f"http://{info.host}:{info.port}/health"
        deadline = time.monotonic() + timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            try:
                resp = await self.http_client.get(url, timeout=httpx.Timeout(5, connect=3))
                if resp.status_code == 200:
                    logger.info("Sandbox %s healthy after %d attempts", info.container_name, attempt)
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except Exception as e:
                logger.debug("Health check attempt %d failed: %s", attempt, e)

            await asyncio.sleep(min(2, timeout / 10))

        return False

    async def _force_remove(self, container_name: str) -> None:
        """Force-remove a container."""
        rc, _, err = await _run(["docker", "rm", "-f", container_name], timeout=30)
        if rc != 0:
            logger.warning("Failed to remove container %s: %s", container_name, err)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_manager: Optional[SandboxManager] = None


def get_sandbox_manager() -> SandboxManager:
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager


async def close_sandbox_manager() -> None:
    global _manager
    if _manager:
        await _manager.destroy_all()
        _manager = None
