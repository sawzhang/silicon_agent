"""Tests for sandbox backend abstraction and SandboxManager dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.sandbox_backend import (
    SandboxCreateResult,
    SandboxInfo,
    SandboxResult,
)


# ---------------------------------------------------------------------------
# SandboxInfo dataclass
# ---------------------------------------------------------------------------


class TestSandboxInfo:
    def test_basic_creation(self) -> None:
        info = SandboxInfo(task_id="t1", sandbox_name="sa-sandbox-t1")
        assert info.task_id == "t1"
        assert info.sandbox_name == "sa-sandbox-t1"
        assert info.extra == {}

    def test_docker_compat_accessors(self) -> None:
        info = SandboxInfo(
            task_id="t1",
            sandbox_name="sa-sandbox-t1",
            extra={
                "container_id": "abc123",
                "host": "172.17.0.2",
                "port": 9090,
            },
        )
        assert info.container_id == "abc123"
        assert info.container_name == "sa-sandbox-t1"
        assert info.host == "172.17.0.2"
        assert info.port == 9090

    def test_missing_docker_fields_return_defaults(self) -> None:
        info = SandboxInfo(task_id="t1", sandbox_name="boxlite-t1")
        assert info.container_id == ""
        assert info.host == ""
        assert info.port == 0


# ---------------------------------------------------------------------------
# Backend selection in SandboxManager
# ---------------------------------------------------------------------------


class TestSandboxManagerBackendSelection:
    def test_docker_backend_by_default(self) -> None:
        with patch("app.worker.sandbox.settings") as mock_settings:
            mock_settings.SANDBOX_BACKEND = "docker"
            mock_settings.SANDBOX_MAX_CONCURRENT = 4
            from app.worker.sandbox import DockerSandboxBackend, _create_backend

            backend = _create_backend()
            assert isinstance(backend, DockerSandboxBackend)

    def test_boxlite_backend_when_configured(self) -> None:
        with patch("app.worker.sandbox.settings") as mock_settings:
            mock_settings.SANDBOX_BACKEND = "boxlite"
            from app.worker.sandbox import _create_backend

            backend = _create_backend()
            # Dynamically imports BoxLiteSandboxBackend
            from app.worker.sandbox_boxlite import BoxLiteSandboxBackend

            assert isinstance(backend, BoxLiteSandboxBackend)

    def test_unknown_backend_falls_back_to_docker(self) -> None:
        with patch("app.worker.sandbox.settings") as mock_settings:
            mock_settings.SANDBOX_BACKEND = "unknown"
            mock_settings.SANDBOX_MAX_CONCURRENT = 4
            from app.worker.sandbox import DockerSandboxBackend, _create_backend

            backend = _create_backend()
            assert isinstance(backend, DockerSandboxBackend)


# ---------------------------------------------------------------------------
# SandboxManager delegates to backend
# ---------------------------------------------------------------------------


class TestSandboxManagerDelegation:
    @pytest.mark.asyncio
    async def test_create_delegates(self) -> None:
        from app.worker.sandbox import SandboxManager

        mock_backend = AsyncMock()
        expected_result = SandboxCreateResult(
            info=SandboxInfo(task_id="t1", sandbox_name="test"),
            workspace="/tmp/ws",
        )
        mock_backend.create = AsyncMock(return_value=expected_result)

        mgr = SandboxManager()
        mgr._backend = mock_backend

        result = await mgr.create("t1", workspace="/tmp/ws")
        assert result is expected_result
        mock_backend.create.assert_called_once_with(
            "t1", workspace="/tmp/ws", workspace_source="fallback", image=None
        )

    @pytest.mark.asyncio
    async def test_execute_stage_delegates(self) -> None:
        from app.worker.sandbox import SandboxManager

        mock_backend = AsyncMock()
        expected_result = SandboxResult(text_content="ok", streamed=True)
        mock_backend.execute_stage = AsyncMock(return_value=expected_result)

        mgr = SandboxManager()
        mgr._backend = mock_backend

        info = SandboxInfo(task_id="t1", sandbox_name="test")
        result = await mgr.execute_stage(
            info,
            system_prompt="sys",
            user_prompt="user",
        )
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_destroy_delegates(self) -> None:
        from app.worker.sandbox import SandboxManager

        mock_backend = AsyncMock()
        mgr = SandboxManager()
        mgr._backend = mock_backend

        await mgr.destroy("t1")
        mock_backend.destroy.assert_called_once_with("t1")

    def test_get_info_delegates(self) -> None:
        from app.worker.sandbox import SandboxManager

        mock_backend = MagicMock()
        expected_info = SandboxInfo(task_id="t1", sandbox_name="test")
        mock_backend.get_info = MagicMock(return_value=expected_info)

        mgr = SandboxManager()
        mgr._backend = mock_backend

        result = mgr.get_info("t1")
        assert result is expected_info
