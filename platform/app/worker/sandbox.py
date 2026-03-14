"""Sandbox lifecycle management with pluggable backends.

Supports two backends, selected via ``SANDBOX_BACKEND`` setting:

- ``"docker"`` (default) — each task gets a Docker container running
  ``agent_server.py``; the platform communicates via HTTP/NDJSON.
- ``"boxlite"`` — each task gets a BoxLite micro-VM; the ``AgentRunner``
  runs in-process on the host, only shell/file I/O enters the VM.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from app.config import settings
from app.integration.skillkit_env import build_sandbox_llm_env
from app.worker.agents import get_all_tools
from app.worker.sandbox_backend import (
    OnSandboxEvent,
    SandboxCreateResult,
    SandboxInfo,
    SandboxResult,
    get_role_resource_profile,
)

logger = logging.getLogger(__name__)
_MODEL_API_LOG_MOUNT_DIR = "/model_api_logs"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


# Semaphore to limit concurrent Docker containers
_docker_concurrency_sem: Optional[asyncio.Semaphore] = None


def _get_docker_semaphore() -> asyncio.Semaphore:
    global _docker_concurrency_sem
    if _docker_concurrency_sem is None:
        _docker_concurrency_sem = asyncio.Semaphore(settings.SANDBOX_MAX_CONCURRENT)
    return _docker_concurrency_sem


# ============================================================================
# Docker backend
# ============================================================================


class DockerSandboxBackend:
    """Sandbox backend that uses Docker containers with an embedded HTTP agent server."""

    def __init__(self) -> None:
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
        role: Optional[str] = None,
        cpus: Optional[int] = None,
        memory_mib: Optional[int] = None,
        mount_mode: str = "rw",
    ) -> SandboxCreateResult:
        """Create a Docker container for a task.

        The *role*, *cpus*, *memory_mib*, and *mount_mode* parameters are
        accepted for interface compatibility with ``BoxLiteSandboxBackend`` but
        are currently ignored by the Docker backend.
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

        sem = _get_docker_semaphore()
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
        workspace_uid: int | None = None
        workspace_gid: int | None = None
        if settings.SANDBOX_RUN_AS_WORKSPACE_OWNER:
            try:
                stat_result = workspace_path.stat()
                workspace_uid = stat_result.st_uid
                workspace_gid = stat_result.st_gid
            except OSError:
                logger.warning("Failed to stat sandbox workspace owner for %s", workspace, exc_info=True)

        docker_cmd = self._build_docker_run_cmd(
            container_name=container_name,
            image=resolved_image,
            workspace=workspace,
            task_id=task_id,
            workspace_uid=workspace_uid,
            workspace_gid=workspace_gid,
        )

        logger.info("Creating sandbox container: %s (image=%s)", container_name, resolved_image)
        rc, out, err = await _run(docker_cmd, timeout=120)

        if rc != 0:
            return _failed(
                error_code="docker_run_failed",
                error_message=err or "docker run failed",
            )

        container_id = out.strip()

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
            task_id=task_id,
            sandbox_name=container_name,
            extra={
                "container_id": container_id,
                "host": host,
                "port": port,
            },
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
        on_event: Optional[OnSandboxEvent] = None,
    ) -> SandboxResult:
        """Execute a stage in the Docker container via HTTP streaming."""
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
            # NOTE: do NOT read e.response.text here — on a streaming response
            # the body has not been buffered and accessing .text raises
            # ResponseNotRead, which would mask the original status code.
            status_code = e.response.status_code
            logger.error(
                "Sandbox HTTP %d from %s (check container logs for root cause)",
                status_code,
                info.container_name,
            )
            return SandboxResult(
                error=(
                    f"Sandbox HTTP {status_code} from {info.container_name} "
                    f"(see container logs for details)"
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
        """Destroy a task's Docker container and release resources."""
        info = self._containers.pop(task_id, None)
        if info:
            await self._force_remove(info.container_name)
            _get_docker_semaphore().release()
            logger.info("Destroyed sandbox container: %s", info.container_name)

    async def destroy_all(self) -> None:
        """Destroy all managed Docker containers."""
        task_ids = list(self._containers.keys())
        for task_id in task_ids:
            await self.destroy(task_id)
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def get_info(self, task_id: str) -> Optional[SandboxInfo]:
        return self._containers.get(task_id)

    # ------------------------------------------------------------------
    # Docker-specific helpers
    # ------------------------------------------------------------------

    def _build_docker_run_cmd(
        self,
        container_name: str,
        image: str,
        workspace: str,
        task_id: str,
        workspace_uid: int | None = None,
        workspace_gid: int | None = None,
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
        if settings.SANDBOX_RUN_AS_WORKSPACE_OWNER and workspace_uid is not None and workspace_gid is not None:
            parts.extend(["--user", f"{workspace_uid}:{workspace_gid}"])
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
                "-e",
                f"GRADLE_USER_HOME={settings.SANDBOX_GRADLE_USER_HOME}",
                "-e",
                f"SANDBOX_GRADLE_WRAPPER_PREWARM={'true' if settings.SANDBOX_GRADLE_WRAPPER_PREWARM else 'false'}",
                "-e",
                f"SANDBOX_GRADLE_WRAPPER_PREWARM_TIMEOUT_SECONDS={int(settings.SANDBOX_GRADLE_WRAPPER_PREWARM_TIMEOUT_SECONDS)}",
            ]
        )
        if capture_model_api_raw and container_raw_log_path:
            parts.extend(["-e", f"SANDBOX_MODEL_API_RAW_LOG_PATH={container_raw_log_path}"])
        parts.extend(
            [
                "-e",
                f"SANDBOX_GRADLE_CMD_TIMEOUT_SECONDS={int(settings.SANDBOX_GRADLE_CMD_TIMEOUT_SECONDS)}",
            ]
        )

        parts.append(image)

        return parts

    async def _resolve_container_host(self, container_name: str) -> Optional[str]:
        """Resolve the container's IP address on the sandbox network."""
        network = settings.SANDBOX_NETWORK

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


# ============================================================================
# SandboxManager — facade that delegates to the configured backend
# ============================================================================


class SandboxManager:
    """Manages sandbox lifecycle, delegating to a pluggable backend.

    Backend selection is driven by ``settings.SANDBOX_BACKEND``:

    - ``"docker"`` (default) — ``DockerSandboxBackend``
    - ``"boxlite"`` — ``BoxLiteSandboxBackend``
    """

    def __init__(self) -> None:
        self._backend = _create_backend()
        self._role_sandboxes: dict[str, SandboxInfo] = {}  # "role:task_id" → SandboxInfo

    # --- Proxy every method to the backend ---

    async def create(
        self,
        task_id: str,
        *,
        workspace: str,
        workspace_source: str = "fallback",
        image: Optional[str] = None,
    ) -> SandboxCreateResult:
        return await self._backend.create(
            task_id,
            workspace=workspace,
            workspace_source=workspace_source,
            image=image,
        )

    async def get_or_create_role_sandbox(
        self,
        task_id: str,
        role: str,
        *,
        workspace: str,
        workspace_source: str = "fallback",
        image: Optional[str] = None,
    ) -> SandboxCreateResult:
        """Get existing sandbox for (role, task) or create a new one.

        Role-specific resource profiles are resolved from
        ``SANDBOX_ROLE_RESOURCES`` configuration.
        """
        cache_key = f"{role}:{task_id}"
        if cache_key in self._role_sandboxes:
            info = self._role_sandboxes[cache_key]
            return SandboxCreateResult(
                info=info,
                workspace=workspace,
                workspace_source=workspace_source,
            )

        profile = get_role_resource_profile(role)
        result = await self._backend.create(
            task_id,
            workspace=workspace,
            workspace_source=workspace_source,
            image=image or profile.image,
            role=role,
            cpus=profile.cpus,
            memory_mib=profile.memory_mib,
            mount_mode=profile.mount_mode,
        )
        if result.info:
            result.info.role = role
            self._role_sandboxes[cache_key] = result.info
        return result

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
        return await self._backend.execute_stage(
            info,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_turns=max_turns,
            enable_tools=enable_tools,
            allowed_tools=allowed_tools,
            skill_dirs=skill_dirs,
            workdir=workdir,
            timeout=timeout,
            on_event=on_event,
        )

    async def destroy(self, task_id: str) -> None:
        await self._backend.destroy(task_id)

    async def destroy_role_sandboxes(self, task_id: str) -> None:
        """Destroy all role sandboxes for a task."""
        keys_to_remove = [k for k in self._role_sandboxes if k.endswith(f":{task_id}")]
        for key in keys_to_remove:
            self._role_sandboxes.pop(key, None)
        # Delegate actual VM/container cleanup to the backend
        await self._backend.destroy(task_id)

    async def destroy_all(self) -> None:
        self._role_sandboxes.clear()
        await self._backend.destroy_all()

    def get_info(self, task_id: str) -> Optional[SandboxInfo]:
        return self._backend.get_info(task_id)


def _create_backend() -> DockerSandboxBackend:
    """Instantiate the configured sandbox backend."""
    backend_name = getattr(settings, "SANDBOX_BACKEND", "docker")
    if backend_name == "boxlite":
        from app.worker.sandbox_boxlite import BoxLiteSandboxBackend

        logger.info("Using BoxLite sandbox backend")
        return BoxLiteSandboxBackend()  # type: ignore[return-value]
    else:
        logger.info("Using Docker sandbox backend")
        return DockerSandboxBackend()


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
