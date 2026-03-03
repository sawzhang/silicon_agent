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


class TestBoxLiteBackendDestroy:
    @pytest.mark.asyncio
    async def test_destroy_removes_runtime(self) -> None:
        backend = BoxLiteSandboxBackend()
        mock_runtime = AsyncMock()
        mock_runtime.destroy = AsyncMock()
        backend._boxes["task-1"] = mock_runtime

        await backend.destroy("task-1")
        mock_runtime.destroy.assert_called_once()
        assert "task-1" not in backend._boxes

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
        backend._boxes = {"t1": rt1, "t2": rt2}

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
        backend._boxes["task-1"] = mock_runtime

        info = backend.get_info("task-1")
        assert info is not None
        assert info.task_id == "task-1"
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
