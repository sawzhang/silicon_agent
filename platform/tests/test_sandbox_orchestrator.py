"""Tests for app.worker.sandbox_orchestrator module."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import sandbox_orchestrator as so


# ---------------------------------------------------------------------------
# _get_settings
# ---------------------------------------------------------------------------


def test_get_settings_returns_engine_settings(monkeypatch):
    fake_settings = SimpleNamespace(SANDBOX_ENABLED=False)
    import app.worker.engine as _engine
    monkeypatch.setattr(_engine, "settings", fake_settings)
    result = so._get_settings()
    assert result is fake_settings


# ---------------------------------------------------------------------------
# _resolve_sandbox_fallback_mode
# ---------------------------------------------------------------------------


class TestResolveSandboxFallbackMode:
    def test_graceful_default(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_FALLBACK_MODE="graceful"))
        assert so._resolve_sandbox_fallback_mode() == "graceful"

    def test_strict_mode(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_FALLBACK_MODE="strict"))
        assert so._resolve_sandbox_fallback_mode() == "strict"

    def test_none_falls_back_to_graceful(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_FALLBACK_MODE=None))
        assert so._resolve_sandbox_fallback_mode() == "graceful"

    def test_invalid_falls_back_to_graceful(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_FALLBACK_MODE="invalid"))
        assert so._resolve_sandbox_fallback_mode() == "graceful"

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_FALLBACK_MODE="  strict  "))
        assert so._resolve_sandbox_fallback_mode() == "strict"


# ---------------------------------------------------------------------------
# _resolve_sandbox_workspace
# ---------------------------------------------------------------------------


class TestResolveSandboxWorkspace:
    def test_returns_existing_workspace(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_WORKSPACE_BASE_DIR="/base"))
        path, source = so._resolve_sandbox_workspace("t-1", "/my/ws", "given")
        assert path == "/my/ws"
        assert source == "given"

    def test_creates_fallback_path(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_WORKSPACE_BASE_DIR="/base"))
        path, source = so._resolve_sandbox_workspace("t-1", None, "given")
        assert path.endswith("/t-1")
        assert "/base/" in path
        assert source == "fallback"


# ---------------------------------------------------------------------------
# _get_sandbox_roles
# ---------------------------------------------------------------------------


class TestGetSandboxRoles:
    def test_parses_json_roles(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_ROLES='["coding","review"]'))
        assert so._get_sandbox_roles() == {"coding", "review"}

    def test_invalid_json_returns_default(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_ROLES="not-json"))
        assert so._get_sandbox_roles() == {"coding", "test"}

    def test_none_returns_default(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_ROLES=None))
        assert so._get_sandbox_roles() == {"coding", "test"}


# ---------------------------------------------------------------------------
# _setup_role_sandbox
# ---------------------------------------------------------------------------


class TestSetupRoleSandbox:
    @pytest.mark.asyncio
    async def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_ENABLED=False))
        task = SimpleNamespace(id="t-1")
        info, mgr, err = await so._setup_role_sandbox(task, "coding", "/ws", "given")
        assert info is None and mgr is None and err is None

    @pytest.mark.asyncio
    async def test_role_not_in_sandbox_roles(self, monkeypatch):
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(SANDBOX_ENABLED=True, SANDBOX_ROLES='["coding"]'),
        )
        task = SimpleNamespace(id="t-1")
        info, mgr, err = await so._setup_role_sandbox(task, "review", "/ws", "given")
        assert info is None and mgr is None and err is None

    @pytest.mark.asyncio
    async def test_success_returns_info(self, monkeypatch):
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_ROLES='["coding"]',
                SANDBOX_WORKSPACE_BASE_DIR="/base",
            ),
        )
        fake_info = SimpleNamespace(container_name="c-1")
        fake_result = SimpleNamespace(info=fake_info, error_code=None, error_message=None)
        fake_mgr = SimpleNamespace(get_or_create_role_sandbox=AsyncMock(return_value=fake_result))

        monkeypatch.setattr(so, "get_sandbox_manager", lambda: fake_mgr, raising=False)
        # Patch lazy import
        monkeypatch.setitem(
            sys.modules, "app.worker.sandbox",
            SimpleNamespace(get_sandbox_manager=lambda: fake_mgr),
        )

        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_role_sandbox(task, "coding", "/ws", "given")
        assert info is fake_info
        assert err is None

    @pytest.mark.asyncio
    async def test_success_with_project_sandbox_image(self, monkeypatch):
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_ROLES='["coding"]',
                SANDBOX_WORKSPACE_BASE_DIR="/base",
            ),
        )
        fake_info = SimpleNamespace(container_name="c-1")
        fake_result = SimpleNamespace(info=fake_info, error_code=None, error_message=None)
        fake_mgr = SimpleNamespace(get_or_create_role_sandbox=AsyncMock(return_value=fake_result))
        monkeypatch.setitem(
            sys.modules, "app.worker.sandbox",
            SimpleNamespace(get_sandbox_manager=lambda: fake_mgr),
        )
        project = SimpleNamespace(sandbox_image="custom:latest")
        task = SimpleNamespace(id="t-1", project=project)
        info, mgr, err = await so._setup_role_sandbox(task, "coding", "/ws", "given")
        assert info is fake_info
        # Verify custom image was passed
        call_kwargs = fake_mgr.get_or_create_role_sandbox.call_args
        assert call_kwargs.kwargs.get("image") == "custom:latest"

    @pytest.mark.asyncio
    async def test_creation_failure_returns_error(self, monkeypatch):
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_ROLES='["coding"]',
                SANDBOX_WORKSPACE_BASE_DIR="/base",
            ),
        )
        fake_mgr = SimpleNamespace(
            get_or_create_role_sandbox=AsyncMock(side_effect=RuntimeError("boom")),
        )
        monkeypatch.setitem(
            sys.modules, "app.worker.sandbox",
            SimpleNamespace(get_sandbox_manager=lambda: fake_mgr),
        )
        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_role_sandbox(task, "coding", "/ws", "given")
        assert info is None
        assert "role_sandbox_create_failed" in err

    @pytest.mark.asyncio
    async def test_result_no_info_returns_error(self, monkeypatch):
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_ROLES='["coding"]',
                SANDBOX_WORKSPACE_BASE_DIR="/base",
            ),
        )
        fake_result = SimpleNamespace(info=None, error_code="no_image", error_message="missing")
        fake_mgr = SimpleNamespace(
            get_or_create_role_sandbox=AsyncMock(return_value=fake_result),
        )
        monkeypatch.setitem(
            sys.modules, "app.worker.sandbox",
            SimpleNamespace(get_sandbox_manager=lambda: fake_mgr),
        )
        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_role_sandbox(task, "coding", "/ws", "given")
        assert info is None
        assert "no_image" in err


# ---------------------------------------------------------------------------
# _setup_sandbox
# ---------------------------------------------------------------------------


def _patch_engine_and_sandbox(monkeypatch, fake_mgr):
    """Helper to patch engine and sandbox modules for _setup_sandbox tests."""
    import app.worker.engine as engine

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "get_sandbox_manager", lambda: fake_mgr, raising=False)

    # Patch the sandbox module's get_sandbox_manager (used by lazy import)
    import app.worker.sandbox as sandbox_mod
    monkeypatch.setattr(sandbox_mod, "get_sandbox_manager", lambda: fake_mgr)


class TestSetupSandbox:
    @pytest.mark.asyncio
    async def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(so, "_get_settings", lambda: SimpleNamespace(SANDBOX_ENABLED=False))
        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_sandbox(task, "/ws", "given")
        assert info is None and mgr is None and err is None

    @pytest.mark.asyncio
    async def test_success_path(self, monkeypatch, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_FALLBACK_MODE="graceful",
                SANDBOX_WORKSPACE_BASE_DIR=str(tmp_path),
                SANDBOX_ROLES='["coding"]',
                SANDBOX_IMAGE="default-image",
            ),
        )
        fake_info = SimpleNamespace(container_name="c-1")
        fake_result = SimpleNamespace(
            info=fake_info, workspace=str(ws_dir), workspace_source="given",
            error_code=None, error_message=None,
        )
        fake_mgr = SimpleNamespace(create=AsyncMock(return_value=fake_result))
        _patch_engine_and_sandbox(monkeypatch, fake_mgr)

        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_sandbox(task, str(ws_dir), "given")
        assert info is fake_info
        assert err is None

    @pytest.mark.asyncio
    async def test_creation_failure_graceful_fallback(self, monkeypatch, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_FALLBACK_MODE="graceful",
                SANDBOX_WORKSPACE_BASE_DIR=str(tmp_path),
                SANDBOX_ROLES='["coding"]',
                SANDBOX_IMAGE="default-image",
            ),
        )
        fake_result = SimpleNamespace(
            info=None, workspace=str(ws_dir), workspace_source="given",
            error_code="create_failed", error_message="timeout",
        )
        fake_mgr = SimpleNamespace(create=AsyncMock(return_value=fake_result))
        _patch_engine_and_sandbox(monkeypatch, fake_mgr)

        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_sandbox(task, str(ws_dir), "given")
        assert info is None
        assert "create_failed" in err

    @pytest.mark.asyncio
    async def test_exception_graceful_fallback(self, monkeypatch, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_FALLBACK_MODE="graceful",
                SANDBOX_WORKSPACE_BASE_DIR=str(tmp_path),
                SANDBOX_ROLES='["coding"]',
                SANDBOX_IMAGE="default-image",
            ),
        )
        fake_mgr = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
        _patch_engine_and_sandbox(monkeypatch, fake_mgr)

        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_sandbox(task, str(ws_dir), "given")
        assert info is None
        assert err == "sandbox_create_exception"

    @pytest.mark.asyncio
    async def test_workspace_not_found_graceful(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_FALLBACK_MODE="graceful",
                SANDBOX_WORKSPACE_BASE_DIR=str(tmp_path),
                SANDBOX_ROLES='["coding"]',
                SANDBOX_IMAGE="default-image",
            ),
        )
        fake_mgr = SimpleNamespace(create=AsyncMock())
        _patch_engine_and_sandbox(monkeypatch, fake_mgr)

        task = SimpleNamespace(id="t-1", project=None)
        # Pass a non-existent workspace path (not fallback)
        info, mgr, err = await so._setup_sandbox(task, "/nonexistent/ws", "given")
        assert info is None
        assert "workspace_not_found" in (err or "")

    @pytest.mark.asyncio
    async def test_success_with_project_sandbox_image(self, monkeypatch, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_FALLBACK_MODE="graceful",
                SANDBOX_WORKSPACE_BASE_DIR=str(tmp_path),
                SANDBOX_ROLES='["coding"]',
                SANDBOX_IMAGE="default-image",
            ),
        )
        fake_info = SimpleNamespace(container_name="c-1")
        fake_result = SimpleNamespace(
            info=fake_info, workspace=str(ws_dir), workspace_source="given",
            error_code=None, error_message=None,
        )
        fake_mgr = SimpleNamespace(create=AsyncMock(return_value=fake_result))
        _patch_engine_and_sandbox(monkeypatch, fake_mgr)

        project = SimpleNamespace(sandbox_image="custom:v2")
        task = SimpleNamespace(id="t-1", project=project)
        info, mgr, err = await so._setup_sandbox(task, str(ws_dir), "given")
        assert info is fake_info
        # Verify custom image was passed
        call_kwargs = fake_mgr.create.call_args
        assert call_kwargs.kwargs.get("image") == "custom:v2"

    @pytest.mark.asyncio
    async def test_exception_strict_mode(self, monkeypatch, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        monkeypatch.setattr(
            so, "_get_settings",
            lambda: SimpleNamespace(
                SANDBOX_ENABLED=True,
                SANDBOX_FALLBACK_MODE="strict",
                SANDBOX_WORKSPACE_BASE_DIR=str(tmp_path),
                SANDBOX_ROLES='["coding"]',
                SANDBOX_IMAGE="default-image",
            ),
        )
        fake_mgr = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
        _patch_engine_and_sandbox(monkeypatch, fake_mgr)

        task = SimpleNamespace(id="t-1", project=None)
        info, mgr, err = await so._setup_sandbox(task, str(ws_dir), "given")
        assert info is None
        assert err == "sandbox_create_exception"
