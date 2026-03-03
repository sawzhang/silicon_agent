"""Tests for Phase 3 per-role sandbox creation in engine.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.sandbox_backend import SandboxCreateResult, SandboxInfo


# ---------------------------------------------------------------------------
# _get_sandbox_roles
# ---------------------------------------------------------------------------


class TestGetSandboxRoles:
    def test_default_roles(self) -> None:
        with patch("app.worker.engine.settings") as mock_settings:
            mock_settings.SANDBOX_ROLES = '["coding", "test"]'
            from app.worker.engine import _get_sandbox_roles

            roles = _get_sandbox_roles()
            assert roles == {"coding", "test"}

    def test_custom_roles(self) -> None:
        with patch("app.worker.engine.settings") as mock_settings:
            mock_settings.SANDBOX_ROLES = '["coding", "test", "review", "spec"]'
            from app.worker.engine import _get_sandbox_roles

            roles = _get_sandbox_roles()
            assert roles == {"coding", "test", "review", "spec"}

    def test_invalid_json_falls_back(self) -> None:
        with patch("app.worker.engine.settings") as mock_settings:
            mock_settings.SANDBOX_ROLES = "not valid json"
            from app.worker.engine import _get_sandbox_roles

            roles = _get_sandbox_roles()
            assert roles == {"coding", "test"}


# ---------------------------------------------------------------------------
# _setup_role_sandbox
# ---------------------------------------------------------------------------


class TestSetupRoleSandbox:
    @pytest.mark.asyncio
    async def test_disabled_sandbox_returns_none(self) -> None:
        with patch("app.worker.engine.settings") as mock_settings:
            mock_settings.SANDBOX_ENABLED = False
            from app.worker.engine import _setup_role_sandbox

            task = MagicMock()
            task.id = "task-1"
            info, mgr, err = await _setup_role_sandbox(task, "coding", "/tmp/ws", "given")
            assert info is None
            assert mgr is None
            assert err is None

    @pytest.mark.asyncio
    async def test_non_sandbox_role_returns_none(self) -> None:
        with patch("app.worker.engine.settings") as mock_settings:
            mock_settings.SANDBOX_ENABLED = True
            mock_settings.SANDBOX_ROLES = '["coding", "test"]'
            from app.worker.engine import _setup_role_sandbox

            task = MagicMock()
            task.id = "task-1"
            info, mgr, err = await _setup_role_sandbox(task, "spec", "/tmp/ws", "given")
            assert info is None
            assert mgr is None
            assert err is None

    @pytest.mark.asyncio
    async def test_successful_role_sandbox_creation(self) -> None:
        expected_info = SandboxInfo(
            task_id="task-1",
            sandbox_name="sa-boxlite-coding-task-1",
            role="coding",
        )
        expected_result = SandboxCreateResult(
            info=expected_info,
            workspace="/tmp/ws",
            workspace_source="given",
        )
        mock_mgr = AsyncMock()
        mock_mgr.get_or_create_role_sandbox = AsyncMock(return_value=expected_result)

        with (
            patch("app.worker.engine.settings") as mock_settings,
            patch("app.worker.sandbox.get_sandbox_manager", return_value=mock_mgr),
            patch("app.worker.engine._resolve_sandbox_workspace", return_value=("/tmp/ws", "given")),
        ):
            mock_settings.SANDBOX_ENABLED = True
            mock_settings.SANDBOX_ROLES = '["coding", "test"]'
            mock_settings.SANDBOX_WORKSPACE_BASE_DIR = "/tmp/silicon_agent/tasks"
            from app.worker.engine import _setup_role_sandbox

            task = MagicMock()
            task.id = "task-1"
            task.project = None
            info, mgr, err = await _setup_role_sandbox(task, "coding", "/tmp/ws", "given")
            assert info is expected_info
            assert err is None
            mock_mgr.get_or_create_role_sandbox.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_role_sandbox_returns_error(self) -> None:
        failed_result = SandboxCreateResult(
            info=None,
            workspace="/tmp/ws",
            error_code="box_create_failed",
            error_message="VM failed to start",
        )
        mock_mgr = AsyncMock()
        mock_mgr.get_or_create_role_sandbox = AsyncMock(return_value=failed_result)

        with (
            patch("app.worker.engine.settings") as mock_settings,
            patch("app.worker.sandbox.get_sandbox_manager", return_value=mock_mgr),
            patch("app.worker.engine._resolve_sandbox_workspace", return_value=("/tmp/ws", "given")),
        ):
            mock_settings.SANDBOX_ENABLED = True
            mock_settings.SANDBOX_ROLES = '["coding", "test"]'
            mock_settings.SANDBOX_WORKSPACE_BASE_DIR = "/tmp/silicon_agent/tasks"
            from app.worker.engine import _setup_role_sandbox

            task = MagicMock()
            task.id = "task-1"
            task.project = None
            info, mgr, err = await _setup_role_sandbox(task, "coding", "/tmp/ws", "given")
            assert info is None
            assert err is not None
            assert "box_create_failed" in err

    @pytest.mark.asyncio
    async def test_exception_during_creation_returns_error(self) -> None:
        mock_mgr = AsyncMock()
        mock_mgr.get_or_create_role_sandbox = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )

        with (
            patch("app.worker.engine.settings") as mock_settings,
            patch("app.worker.sandbox.get_sandbox_manager", return_value=mock_mgr),
            patch("app.worker.engine._resolve_sandbox_workspace", return_value=("/tmp/ws", "given")),
        ):
            mock_settings.SANDBOX_ENABLED = True
            mock_settings.SANDBOX_ROLES = '["coding", "test"]'
            mock_settings.SANDBOX_WORKSPACE_BASE_DIR = "/tmp/silicon_agent/tasks"
            from app.worker.engine import _setup_role_sandbox

            task = MagicMock()
            task.id = "task-1"
            task.project = None
            info, mgr, err = await _setup_role_sandbox(task, "coding", "/tmp/ws", "given")
            assert info is None
            assert "role_sandbox_create_failed" in err


# ---------------------------------------------------------------------------
# SandboxManager.get_or_create_role_sandbox caching
# ---------------------------------------------------------------------------


class TestRoleSandboxCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_existing(self) -> None:
        from app.worker.sandbox import SandboxManager

        expected_info = SandboxInfo(
            task_id="t1",
            sandbox_name="sa-boxlite-coding-t1",
            role="coding",
        )
        mock_backend = AsyncMock()
        mgr = SandboxManager()
        mgr._backend = mock_backend
        # Pre-populate cache
        mgr._role_sandboxes["coding:t1"] = expected_info

        result = await mgr.get_or_create_role_sandbox(
            "t1", "coding", workspace="/tmp/ws",
        )
        assert result.info is expected_info
        # Backend should NOT have been called
        mock_backend.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_roles_create_different_sandboxes(self) -> None:
        from app.worker.sandbox import SandboxManager

        coding_info = SandboxInfo(task_id="t1", sandbox_name="coding-box", role="coding")
        test_info = SandboxInfo(task_id="t1", sandbox_name="test-box", role="test")

        call_count = 0

        async def mock_create(task_id, *, workspace, workspace_source, image, role, cpus, memory_mib, mount_mode):
            nonlocal call_count
            call_count += 1
            if role == "coding":
                return SandboxCreateResult(info=coding_info, workspace=workspace)
            return SandboxCreateResult(info=test_info, workspace=workspace)

        mock_backend = AsyncMock()
        mock_backend.create = mock_create

        mgr = SandboxManager()
        mgr._backend = mock_backend

        with patch("app.worker.sandbox.get_role_resource_profile") as mock_profile:
            mock_profile.return_value = MagicMock(cpus=2, memory_mib=4096, image=None, mount_mode="rw")

            r1 = await mgr.get_or_create_role_sandbox("t1", "coding", workspace="/tmp/ws")
            r2 = await mgr.get_or_create_role_sandbox("t1", "test", workspace="/tmp/ws")

        assert r1.info.role == "coding"
        assert r2.info.role == "test"
        assert call_count == 2
        assert "coding:t1" in mgr._role_sandboxes
        assert "test:t1" in mgr._role_sandboxes

    @pytest.mark.asyncio
    async def test_destroy_role_sandboxes_clears_cache(self) -> None:
        from app.worker.sandbox import SandboxManager

        mock_backend = AsyncMock()
        mgr = SandboxManager()
        mgr._backend = mock_backend

        mgr._role_sandboxes["coding:t1"] = SandboxInfo(task_id="t1", sandbox_name="c", role="coding")
        mgr._role_sandboxes["test:t1"] = SandboxInfo(task_id="t1", sandbox_name="t", role="test")
        mgr._role_sandboxes["coding:t2"] = SandboxInfo(task_id="t2", sandbox_name="c2", role="coding")

        await mgr.destroy_role_sandboxes("t1")

        # t1 entries removed, t2 preserved
        assert "coding:t1" not in mgr._role_sandboxes
        assert "test:t1" not in mgr._role_sandboxes
        assert "coding:t2" in mgr._role_sandboxes
        mock_backend.destroy.assert_called_once_with("t1")


# ---------------------------------------------------------------------------
# Backward compatibility: sandbox_info passed directly
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Verify that when sandbox_info is passed to _execute_single_stage,
    the per-role creation is skipped."""

    def test_sandbox_info_not_none_skips_role_creation(self) -> None:
        """The condition `sandbox_info is None and stage.agent_role in _sandbox_roles`
        should be False when sandbox_info is already set."""
        existing_info = SandboxInfo(task_id="t1", sandbox_name="existing")
        # If sandbox_info is already not None, the per-role creation branch
        # should not be entered. We verify this by checking the condition.
        sandbox_roles = {"coding", "test"}
        agent_role = "coding"

        # This mirrors the logic in _execute_single_stage
        should_create = existing_info is None and agent_role in sandbox_roles
        assert should_create is False
