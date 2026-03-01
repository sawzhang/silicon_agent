"""Tests for core engine functions: circuit breaker, task claim, state transitions, gates."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db.session import async_session_factory
from app.models.task import TaskModel
from app.worker import engine


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_stage(**kw) -> SimpleNamespace:
    defaults = dict(
        id="stage-1",
        stage_name="coding",
        agent_role="coding",
        error_message=None,
        output_summary=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_task(**kw) -> SimpleNamespace:
    defaults = dict(
        id="task-1",
        title="Test Task",
        status="running",
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)




@pytest.mark.asyncio
async def test_cleanup_runtime_resources_worktree_success(monkeypatch):
    """worktree_mgr + worktree_path → calls cleanup_worktree successfully."""
    cleanup_calls = []
    task_id = "tt-cleanup-wt-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Cleanup Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        class FakeWorktreeMgr:
            async def cleanup_worktree(self, tid):
                cleanup_calls.append(tid)

        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            FakeWorktreeMgr(),
            "/fake/worktree",
            None,
            "worktree",
            None,
            None,
        )

    assert len(cleanup_calls) == 1

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_worktree_failure(monkeypatch):
    """worktree cleanup failure → catches exception, does not raise."""
    task_id = "tt-cleanup-wt-fail-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Cleanup Fail Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        class FailingWorktreeMgr:
            async def cleanup_worktree(self, tid):
                raise RuntimeError("cleanup exploded")

        # Should not raise
        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            FailingWorktreeMgr(),
            "/fake/worktree",
            None,
            "worktree",
            None,
            None,
        )

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_sandbox_destroy_raises(monkeypatch, tmp_path):
    """sandbox_mgr + sandbox_info → calls destroy; destroy raises → caught silently."""
    task_id = "tt-cleanup-sandbox-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Sandbox Cleanup Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        class FailingSandboxMgr:
            async def destroy(self, tid):
                raise RuntimeError("container gone")

        sandbox_info = SimpleNamespace(container_name="test-container")

        # Should not raise
        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            None,
            None,
            None,
            "unresolved",
            FailingSandboxMgr(),
            sandbox_info,
        )

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_tmp_workspace_removed(monkeypatch, tmp_path):
    """workspace_source starts with 'tmp_' → shutil.rmtree called on the workspace."""
    import shutil

    rmtree_calls = []

    def _fake_rmtree(p, ignore_errors=False):
        rmtree_calls.append(str(p))

    task_id = "tt-cleanup-tmp-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Tmp Cleanup Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        import tempfile
        from pathlib import Path

        # Create a real subdirectory under the expected base
        tasks_base = Path(tempfile.gettempdir()) / "silicon_agent" / "tasks"
        ws_path = tasks_base / task_id
        ws_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(shutil, "rmtree", _fake_rmtree)

        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            None,
            None,
            str(ws_path),
            "tmp_empty",
            None,
            None,
        )

    # The rmtree should have been called on our ws path
    assert any(task_id in p for p in rmtree_calls)

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ── 15. _finalize_task_resources paths ────────────────────────────────────


@pytest.mark.asyncio
async def test_finalize_task_resources_memory_extraction_success(monkeypatch):
    """MEMORY_ENABLED=True with valid project_memory_store and prior_outputs → extracts memories."""
    extract_mock = AsyncMock()
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr("app.worker.memory_extractor.extract_and_store_memories", extract_mock)

    task_id = "tt-finalize-mem-1"
    async with async_session_factory() as session:
        session.add(TaskModel(
            id=task_id, title="Memory Test", status="running",
            project_id=None,
        ))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        task.project_id = "proj-123"

        project_memory_store = SimpleNamespace(get_memory_for_role=lambda role: "memories")
        prior_outputs = [{"stage": "parse", "output": "parsed output"}]

        result = await engine._finalize_task_resources(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            prior_outputs,
            project_memory_store,
            None, None, None, "unresolved", None, None, None,
        )

    assert result is True
    extract_mock.assert_awaited_once()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_finalize_task_resources_memory_extraction_fails(monkeypatch):
    """Memory extraction fails → exception caught, logs failure, returns True anyway."""
    async def _raising(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr("app.worker.memory_extractor.extract_and_store_memories", _raising)

    task_id = "tt-finalize-mem-fail-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Memory Fail Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        task.project_id = "proj-456"

        project_memory_store = SimpleNamespace()
        prior_outputs = [{"stage": "parse", "output": "text"}]

        # Should not raise, returns True
        result = await engine._finalize_task_resources(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            prior_outputs,
            project_memory_store,
            None, None, None, "unresolved", None, None, None,
        )

    assert result is True

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_finalize_task_resources_commit_push_success(monkeypatch):
    """repo_url set + workspace_path set → commit_and_push_workspace called, returns True."""
    commit_push_mock = AsyncMock(return_value="feat/branch-123")
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "commit_and_push_workspace", commit_push_mock)
    # Also patch create_pr_for_workspace to prevent HTTP calls
    monkeypatch.setattr(engine, "create_pr_for_workspace", AsyncMock(return_value=None))

    # Use a SimpleNamespace task so we can freely set .project
    task = _make_task(id="tt-finalize-commit-sn-1", title="Commit Push Test")
    task.project = SimpleNamespace(
        repo_url="https://github.com/test/repo",
        branch="main",
    )
    task.target_branch = None

    session = SimpleNamespace(commit=AsyncMock())

    result = await engine._finalize_task_resources(
        session,  # type: ignore[arg-type]
        task,  # type: ignore[arg-type]
        [],
        None,
        None,        # worktree_mgr
        None,        # worktree_path
        "/tmp/test_ws",  # workspace_path
        "tmp_cloned",    # workspace_source
        "feat/branch-123",  # workspace_branch
        None,        # sandbox_mgr
        None,        # sandbox_info
    )

    assert result is True
    commit_push_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_task_resources_commit_push_fails(monkeypatch):
    """Worktree commit/push fails → _fail_task called, returns False."""
    fail_task_mock = AsyncMock()
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", fail_task_mock)

    async def _raising_commit(*a, **kw):
        raise RuntimeError("push failed: auth error")

    monkeypatch.setattr(engine, "commit_and_push_workspace", _raising_commit)

    # Use a SimpleNamespace task so we can freely set .project
    task = _make_task(id="tt-finalize-commit-fail-sn-1", title="Commit Fail Test")
    task.project = SimpleNamespace(
        repo_url="https://github.com/test/repo",
        branch="main",
    )
    task.target_branch = None

    session = SimpleNamespace(commit=AsyncMock())

    result = await engine._finalize_task_resources(
        session,  # type: ignore[arg-type]
        task,  # type: ignore[arg-type]
        [],
        None,
        None, None, "/tmp/test_ws", "tmp_cloned", None,
        None, None,
    )

    assert result is False
    fail_task_mock.assert_awaited_once()


# ── 16. _execute_single_stage paths ───────────────────────────────────────
# Section 22: _ensure_code_stage_has_changes additional paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ensure_code_stage_no_changes_false(monkeypatch):
    """Stage is 'code', changed=False → mark failed and fail task, returns False."""
    monkeypatch.setattr(engine, "_has_git_worktree_changes", AsyncMock(return_value=False))
    mark_failed = AsyncMock()
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task()
    stage = _make_stage(stage_name="code")
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._ensure_code_stage_has_changes(session, task, stage, "/some/path")

    assert result is False
    mark_failed.assert_awaited_once()
    fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_code_stage_no_changes_none(monkeypatch):
    """Stage is 'code', changed=None → mark failed and fail task, returns False."""
    monkeypatch.setattr(engine, "_has_git_worktree_changes", AsyncMock(return_value=None))
    mark_failed = AsyncMock()
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task()
    stage = _make_stage(stage_name="code")
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._ensure_code_stage_has_changes(session, task, stage, "/some/path")

    assert result is False
    assert "verification failed" in mark_failed.call_args[0][3]


@pytest.mark.asyncio
async def test_ensure_code_stage_not_code_stage(monkeypatch):
    """Stage is not 'code' → returns True immediately without checking git."""
    git_check = AsyncMock()
    monkeypatch.setattr(engine, "_has_git_worktree_changes", git_check)

    task = _make_task()
    stage = _make_stage(stage_name="review")
    session = SimpleNamespace()

    result = await engine._ensure_code_stage_has_changes(session, task, stage, "/some/path")
    assert result is True
    git_check.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Section 23: _setup_worktree paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_setup_worktree_disabled(monkeypatch):
    """WORKTREE_ENABLED=False → returns (None, None) immediately."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    task = _make_task(project=None)
    path, mgr = await engine._setup_worktree(task)
    assert path is None
    assert mgr is None


@pytest.mark.asyncio
async def test_setup_worktree_no_repo_url(monkeypatch):
    """WORKTREE_ENABLED=True, project has no repo_url and no local_path → returns (None, None)."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    task = _make_task(project=SimpleNamespace(
        id="proj-1", repo_local_path=None, repo_url=None, branch="main",
        target_branch=None,
    ))
    path, mgr = await engine._setup_worktree(task)
    assert path is None
    assert mgr is None


@pytest.mark.asyncio
async def test_setup_worktree_exception(monkeypatch):
    """WORKTREE_ENABLED=True, create_worktree raises → returns (None, worktree_mgr)."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())

    class FakeMgr:
        async def create_worktree(self, **kw):
            raise RuntimeError("git failure")

    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    task = _make_task(project=SimpleNamespace(
        id="proj-1",
        repo_local_path="/existing/repo",
        repo_url="https://github.com/org/repo.git",
        branch="main",
        target_branch=None,
    ))
    monkeypatch.setattr(engine, "Path", lambda p: type("P", (), {"exists": lambda self: True})())

    class _FakePath:
        def __init__(self, p):
            self._p = p
        def exists(self):
            return True
        def __truediv__(self, other):
            return self

    monkeypatch.setattr(engine, "Path", _FakePath)

    path, mgr = await engine._setup_worktree(task)
    assert path is None


@pytest.mark.asyncio
async def test_setup_worktree_returns_none_path(monkeypatch):
    """WORKTREE_ENABLED=True, create_worktree returns None → returns (None, None)."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())

    class FakeMgr:
        async def create_worktree(self, **kw):
            return None

    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    class _FakePath:
        def __init__(self, p):
            self._p = p
        def exists(self):
            return True
        def __truediv__(self, other):
            return self

    monkeypatch.setattr(engine, "Path", _FakePath)

    task = _make_task(
        target_branch=None,
        project=SimpleNamespace(
            id="proj-1",
            repo_local_path="/existing/repo",
            repo_url="https://github.com/org/repo.git",
            branch="main",
        ),
    )
    path, mgr = await engine._setup_worktree(task)
    assert path is None
    assert mgr is None


# ═══════════════════════════════════════════════════════════════════════
# Section 24: _prepare_runtime_workspace paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prepare_runtime_workspace_worktree_path(monkeypatch):
    """worktree_path provided → return immediately with 'worktree' source."""
    task = _make_task(project=None, target_branch=None)
    path, source, branch = await engine._prepare_runtime_workspace(task, "/wt/path")
    assert path == "/wt/path"
    assert source == "worktree"


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_no_repo_url(monkeypatch):
    """No repo_url → return tmp path with 'tmp_empty' source."""
    task = _make_task(project=SimpleNamespace(repo_url="", branch="main"), target_branch=None)
    path, source, branch = await engine._prepare_runtime_workspace(task, None)
    assert source == "tmp_empty"
    assert path is not None


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_worktree_required(monkeypatch):
    """WORKTREE_ENABLED=True, no worktree_path, but has repo_url → 'worktree_required'."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        target_branch=None,
    )
    path, source, branch = await engine._prepare_runtime_workspace(task, None)
    assert path is None
    assert source == "worktree_required"


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_clone_failed(monkeypatch):
    """prepare_workspace_from_repo returns (False, ...) → 'tmp_clone_failed'."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    monkeypatch.setattr(engine, "prepare_workspace_from_repo", AsyncMock(return_value=(False, None, "clone error")))

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        target_branch=None,
    )
    path, source, branch = await engine._prepare_runtime_workspace(task, None)
    assert path is None
    assert source == "tmp_clone_failed"


# ═══════════════════════════════════════════════════════════════════════
# Section 25: _setup_sandbox paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_setup_sandbox_disabled(monkeypatch):
    """SANDBOX_ENABLED=False → returns (None, None, None)."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    task = _make_task(project=None)
    result = await engine._setup_sandbox(task, "/workspace", "tmp_empty")
    assert result == (None, None, None)


@pytest.mark.asyncio
async def test_setup_sandbox_workspace_not_found(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, workspace_path doesn't exist → error returned."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "graceful")
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path / "nodir"), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "graceful")

    class FakeCreateResult:
        info = None
        workspace = str(tmp_path / "nodir")
        workspace_source = "given"
        error_code = "workspace_not_found"
        error_message = "not found"

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            return FakeCreateResult()

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=FakeCreateResult,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path / "nodir"), "given")
        # workspace_not_found → no sandbox_info
        assert sandbox_info is None
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


@pytest.mark.asyncio
async def test_setup_sandbox_success(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, successful sandbox creation."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "graceful")

    workspace = tmp_path
    workspace.mkdir(exist_ok=True)

    class FakeSandboxInfo:
        container_name = "container-1"

    class FakeCreateResult:
        info = FakeSandboxInfo()
        workspace = str(tmp_path)
        workspace_source = "given"
        error_code = None
        error_message = None

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            return FakeCreateResult()

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=FakeCreateResult,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path), "given")
        assert sandbox_info is not None
        assert error is None
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


@pytest.mark.asyncio
async def test_setup_sandbox_exception_graceful(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, sandbox creation raises, graceful fallback."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "graceful")

    tmp_path.mkdir(exist_ok=True)

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            raise RuntimeError("docker down")

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=SimpleNamespace,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path), "given")
        assert sandbox_info is None
        assert error == "sandbox_create_exception"
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


@pytest.mark.asyncio
async def test_setup_sandbox_exception_strict(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, sandbox creation raises, strict mode."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "strict")

    tmp_path.mkdir(exist_ok=True)

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            raise RuntimeError("docker down")

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=SimpleNamespace,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path), "given")
        assert error == "sandbox_create_exception"
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


# ═══════════════════════════════════════════════════════════════════════
# Section 26: _finalize_task_resources paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_finalize_task_resources_skill_feedback(monkeypatch):
    """SKILL_FEEDBACK_ENABLED=True → aggregate_skill_metrics called."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())

    skill_mock = AsyncMock()
    import sys
    sys.modules["app.services.skill_feedback_service"] = SimpleNamespace(aggregate_skill_metrics=skill_mock)

    try:
        task = _make_task(project=None, project_id=None, branch_name=None, pr_url=None, target_branch=None)
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._finalize_task_resources(
            session, task, [], None, None, None, None, "tmp_empty", None, None, None,
        )
        assert result is True
        skill_mock.assert_awaited_once()
    finally:
        sys.modules.pop("app.services.skill_feedback_service", None)


@pytest.mark.asyncio
async def test_finalize_task_resources_skill_feedback_exception(monkeypatch):
    """SKILL_FEEDBACK_ENABLED=True but aggregate_skill_metrics raises → swallowed."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())

    skill_mock = AsyncMock(side_effect=RuntimeError("db error"))
    import sys
    sys.modules["app.services.skill_feedback_service"] = SimpleNamespace(aggregate_skill_metrics=skill_mock)

    try:
        task = _make_task(project=None, project_id=None, branch_name=None, pr_url=None, target_branch=None)
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._finalize_task_resources(
            session, task, [], None, None, None, None, "tmp_empty", None, None, None,
        )
        assert result is True  # exception swallowed
    finally:
        sys.modules.pop("app.services.skill_feedback_service", None)


@pytest.mark.asyncio
async def test_finalize_task_resources_worktree_commit_and_pr(monkeypatch):
    """repo_url + worktree_mgr → commit_and_push + create_pr called."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())

    class FakeWorktreeMgr:
        async def commit_and_push(self, **kw):
            return "feat/my-branch"
        async def create_pr(self, **kw):
            return "https://github.com/org/repo/pull/1"

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        project_id="proj-1", branch_name=None, pr_url=None, target_branch=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    result = await engine._finalize_task_resources(
        session, task, [], None,
        FakeWorktreeMgr(), "/wt/path", "/wt/path", "worktree", "feat/main",
        None, None,
    )
    assert result is True
    assert task.pr_url == "https://github.com/org/repo/pull/1"


@pytest.mark.asyncio
async def test_finalize_task_resources_worktree_commit_exception(monkeypatch):
    """repo_url + worktree_mgr but commit raises → task fails, returns False."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    class BrokenMgr:
        async def commit_and_push(self, **kw):
            raise RuntimeError("push failed")

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        project_id="proj-1", branch_name=None, pr_url=None, target_branch=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    result = await engine._finalize_task_resources(
        session, task, [], None,
        BrokenMgr(), "/wt/path", "/wt/path", "worktree", None,
        None, None,
    )
    assert result is False
    fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_task_resources_workspace_commit_and_pr(monkeypatch):
    """repo_url + no worktree_mgr → commit_and_push_workspace + create_pr_for_workspace."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "commit_and_push_workspace", AsyncMock(return_value="feat/branch"))
    monkeypatch.setattr(engine, "create_pr_for_workspace", AsyncMock(return_value="https://pr/123"))

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        project_id="proj-1", branch_name=None, pr_url=None, target_branch=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    result = await engine._finalize_task_resources(
        session, task, [], None,
        None, None, "/tmp/workspace", "tmp_cloned", "feat/branch",
        None, None,
    )
    assert result is True
    assert task.pr_url == "https://pr/123"


# ═══════════════════════════════════════════════════════════════════════
# Section 27: _cleanup_runtime_resources workspace exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cleanup_runtime_resources_workspace_exception(monkeypatch, tmp_path):
    """workspace_source starts with 'tmp_' but cleanup raises → exception swallowed."""
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import shutil as shutil_mod
    original_rmtree = shutil_mod.rmtree

    def _fail_rmtree(path, **kw):
        raise PermissionError("cannot delete")

    monkeypatch.setattr(shutil_mod, "rmtree", _fail_rmtree)
    monkeypatch.setattr(engine, "shutil", shutil_mod)

    # Create workspace inside expected path hierarchy
    task_root = tmp_path / "silicon_agent" / "tasks"
    task_root.mkdir(parents=True)
    fake_workspace = task_root / "task-999"
    fake_workspace.mkdir()

    # Override tempfile.gettempdir to return tmp_path
    import tempfile as tempfile_mod
    original_gettempdir = tempfile_mod.gettempdir
    monkeypatch.setattr(tempfile_mod, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "tempfile", tempfile_mod)

    task = _make_task()
    # must not raise
    await engine._cleanup_runtime_resources(
        task, None, None, str(fake_workspace), "tmp_cloned", None, None
    )

    monkeypatch.setattr(shutil_mod, "rmtree", original_rmtree)
    monkeypatch.setattr(tempfile_mod, "gettempdir", original_gettempdir)


# ═══════════════════════════════════════════════════════════════════════
