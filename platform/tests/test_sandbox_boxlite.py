"""Tests for BoxLiteSandboxBackend."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock boxlite SDK AND skillkit.runtime.boxlite so they can be imported
# even when the BoxLite SDK is not installed.
# ---------------------------------------------------------------------------

if "boxlite" not in sys.modules:
    _mock_boxlite = types.ModuleType("boxlite")
    _mock_boxlite.Boxlite = MagicMock  # type: ignore[attr-defined]
    _mock_boxlite.Box = MagicMock  # type: ignore[attr-defined]
    _mock_boxlite.BoxOptions = MagicMock  # type: ignore[attr-defined]
    _mock_boxlite.ExecResult = MagicMock  # type: ignore[attr-defined]
    _mock_boxlite.Execution = MagicMock  # type: ignore[attr-defined]
    sys.modules["boxlite"] = _mock_boxlite

# Create a stub skillkit.runtime.boxlite module if it doesn't exist
if "skillkit.runtime.boxlite" not in sys.modules:
    _stub_boxlite_mod = types.ModuleType("skillkit.runtime.boxlite")
    _stub_boxlite_mod.BoxLiteRuntime = MagicMock  # type: ignore[attr-defined]
    _stub_boxlite_mod.SecurityLevel = MagicMock  # type: ignore[attr-defined]
    sys.modules["skillkit.runtime.boxlite"] = _stub_boxlite_mod
    # Also make it accessible via attribute on the parent module
    import skillkit.runtime as _rt_mod  # noqa: E402
    _rt_mod.boxlite = _stub_boxlite_mod  # type: ignore[attr-defined]

# Now we can also stub skillkit.sandbox.runner for execute_stage tests
if "skillkit.sandbox" not in sys.modules:
    _stub_sandbox_mod = types.ModuleType("skillkit.sandbox")
    sys.modules["skillkit.sandbox"] = _stub_sandbox_mod
if "skillkit.sandbox.runner" not in sys.modules:
    _stub_runner_mod = types.ModuleType("skillkit.sandbox.runner")
    _stub_runner_mod.SandboxedAgentRunner = MagicMock  # type: ignore[attr-defined]
    sys.modules["skillkit.sandbox.runner"] = _stub_runner_mod

from app.worker.sandbox_boxlite import BoxLiteSandboxBackend, _register_event_bridge  # noqa: E402


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestBoxLiteBackendCreate:
    @pytest.mark.asyncio
    async def test_workspace_not_found(self) -> None:
        backend = BoxLiteSandboxBackend()
        result = await backend.create(
            "task-1",
            workspace="/nonexistent/path",
        )
        assert result.error_code == "workspace_not_found"
        assert result.info is None

    @pytest.mark.asyncio
    async def test_successful_create(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime = AsyncMock()
        mock_runtime.start = AsyncMock()
        mock_runtime.is_ready = AsyncMock(return_value=True)
        mock_runtime.destroy = AsyncMock()

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            return_value=mock_runtime,
        ):
            result = await backend.create("task-1", workspace=workspace)

        assert result.error_code is None
        assert result.info is not None
        assert result.info.task_id == "task-1"
        assert "boxlite" in result.info.sandbox_name
        assert result.info.extra["runtime"] is mock_runtime
        mock_runtime.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_unhealthy_box_cleaned_up(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime = AsyncMock()
        mock_runtime.start = AsyncMock()
        mock_runtime.is_ready = AsyncMock(return_value=False)
        mock_runtime.destroy = AsyncMock()

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            return_value=mock_runtime,
        ):
            result = await backend.create("task-2", workspace=workspace)

        assert result.error_code == "box_unhealthy"
        mock_runtime.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_failure_returns_error(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime = AsyncMock()
        mock_runtime.start = AsyncMock(side_effect=RuntimeError("VM failed to start"))

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            return_value=mock_runtime,
        ):
            result = await backend.create("task-3", workspace=workspace)

        assert result.error_code == "box_create_failed"
        assert "VM failed to start" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------


class TestBoxLiteBackendCreateWithRole:
    """Tests for create() with role, cpus, memory_mib, mount_mode parameters."""

    @pytest.mark.asyncio
    async def test_create_with_role_uses_composite_key(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime = AsyncMock()
        mock_runtime.start = AsyncMock()
        mock_runtime.is_ready = AsyncMock(return_value=True)
        mock_runtime.destroy = AsyncMock()

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            return_value=mock_runtime,
        ):
            result = await backend.create(
                "task-1", workspace=workspace, role="coding",
            )

        assert result.info is not None
        assert result.info.role == "coding"
        assert "coding" in result.info.sandbox_name
        # Stored under composite key
        assert "coding:task-1" in backend._boxes

    @pytest.mark.asyncio
    async def test_create_with_custom_resources(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime = AsyncMock()
        mock_runtime.start = AsyncMock()
        mock_runtime.is_ready = AsyncMock(return_value=True)

        captured_args: dict = {}

        def capture_runtime(**kwargs):
            captured_args.update(kwargs)
            return mock_runtime

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            side_effect=capture_runtime,
        ):
            result = await backend.create(
                "task-2",
                workspace=workspace,
                role="test",
                cpus=4,
                memory_mib=8192,
                mount_mode="ro",
            )

        assert result.info is not None
        assert captured_args["cpus"] == 4
        assert captured_args["memory_mib"] == 8192
        assert captured_args["volumes"] == [(workspace, "/workspace", "ro")]

    @pytest.mark.asyncio
    async def test_create_without_role_uses_default_key(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime = AsyncMock()
        mock_runtime.start = AsyncMock()
        mock_runtime.is_ready = AsyncMock(return_value=True)

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            return_value=mock_runtime,
        ):
            result = await backend.create("task-3", workspace=workspace)

        assert result.info is not None
        assert "default" in result.info.sandbox_name
        assert "default:task-3" in backend._boxes

    @pytest.mark.asyncio
    async def test_different_roles_same_task_separate_vms(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        backend = BoxLiteSandboxBackend()

        mock_runtime_1 = AsyncMock()
        mock_runtime_1.start = AsyncMock()
        mock_runtime_1.is_ready = AsyncMock(return_value=True)

        mock_runtime_2 = AsyncMock()
        mock_runtime_2.start = AsyncMock()
        mock_runtime_2.is_ready = AsyncMock(return_value=True)

        runtimes = iter([mock_runtime_1, mock_runtime_2])

        with patch(
            "skillkit.runtime.boxlite.BoxLiteRuntime",
            side_effect=lambda **kw: next(runtimes),
        ):
            r1 = await backend.create("task-4", workspace=workspace, role="coding")
            r2 = await backend.create("task-4", workspace=workspace, role="test")

        assert r1.info is not None
        assert r2.info is not None
        assert r1.info.sandbox_name != r2.info.sandbox_name
        assert "coding:task-4" in backend._boxes
        assert "test:task-4" in backend._boxes


class TestBoxLiteBackendDestroy:
    @pytest.mark.asyncio
    async def test_destroy_removes_all_role_runtimes_for_task(self) -> None:
        backend = BoxLiteSandboxBackend()
        rt_coding = AsyncMock()
        rt_coding.destroy = AsyncMock()
        rt_test = AsyncMock()
        rt_test.destroy = AsyncMock()
        rt_other = AsyncMock()
        rt_other.destroy = AsyncMock()
        backend._boxes["coding:task-1"] = rt_coding
        backend._boxes["test:task-1"] = rt_test
        backend._boxes["coding:task-2"] = rt_other

        await backend.destroy("task-1")
        rt_coding.destroy.assert_called_once()
        rt_test.destroy.assert_called_once()
        rt_other.destroy.assert_not_called()
        assert "coding:task-1" not in backend._boxes
        assert "test:task-1" not in backend._boxes
        assert "coding:task-2" in backend._boxes

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_task_is_noop(self) -> None:
        backend = BoxLiteSandboxBackend()
        await backend.destroy("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_destroy_all(self) -> None:
        backend = BoxLiteSandboxBackend()
        rt1 = AsyncMock()
        rt1.destroy = AsyncMock()
        rt2 = AsyncMock()
        rt2.destroy = AsyncMock()
        backend._boxes = {"coding:t1": rt1, "test:t2": rt2}

        await backend.destroy_all()
        rt1.destroy.assert_called_once()
        rt2.destroy.assert_called_once()
        assert len(backend._boxes) == 0


# ---------------------------------------------------------------------------
# get_info
# ---------------------------------------------------------------------------


class TestBoxLiteBackendGetInfo:
    def test_get_info_returns_none_for_unknown(self) -> None:
        backend = BoxLiteSandboxBackend()
        assert backend.get_info("nonexistent") is None

    def test_get_info_returns_info_for_known(self) -> None:
        backend = BoxLiteSandboxBackend()
        mock_runtime = MagicMock()
        backend._boxes["coding:task-1"] = mock_runtime

        info = backend.get_info("task-1")
        assert info is not None
        assert info.task_id == "task-1"
        assert info.role == "coding"
        assert info.extra["runtime"] is mock_runtime


# ---------------------------------------------------------------------------
# Event bridge
# ---------------------------------------------------------------------------


class TestEventBridge:
    @pytest.mark.asyncio
    async def test_registers_all_event_types(self) -> None:
        events = MagicMock()
        runner = MagicMock()
        runner.events = events

        collected: list[dict] = []

        async def on_event(evt: dict) -> None:
            collected.append(evt)

        cleanup = _register_event_bridge(runner, on_event)

        # Should have registered 5 event types
        assert events.on.call_count == 5
        registered_event_names = [call.args[0] for call in events.on.call_args_list]
        assert "turn_start" in registered_event_names
        assert "turn_end" in registered_event_names
        assert "before_tool_call" in registered_event_names
        assert "tool_execution_update" in registered_event_names
        assert "after_tool_result" in registered_event_names

        cleanup()

    def test_none_on_event_returns_noop(self) -> None:
        runner = MagicMock()
        runner.events = MagicMock()
        cleanup = _register_event_bridge(runner, None)
        runner.events.on.assert_not_called()
        cleanup()

    def test_no_events_attr_returns_noop(self) -> None:
        runner = MagicMock(spec=[])
        cleanup = _register_event_bridge(runner, lambda x: None)
        cleanup()
