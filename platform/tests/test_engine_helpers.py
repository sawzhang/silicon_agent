"""Tests for core engine functions: circuit breaker, task claim, state transitions, gates."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
async def test_pick_pending_task_no_pending():
    """When no pending tasks, returns None."""
    async with async_session_factory() as session:
        result = await engine._pick_pending_task(session)
    assert result is None


@pytest.mark.asyncio
async def test_pick_pending_task_claims_oldest():
    """Oldest pending task is claimed atomically."""
    task_id = "tt-pick-pending-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Pick Me", status="pending"))
        await session.commit()

    async with async_session_factory() as session:
        result = await engine._pick_pending_task(session)
        assert result is not None
        assert result.id == task_id
        assert result.status == "claimed"

    # Cleanup
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_pick_pending_task_concurrent_claim():
    """Only one worker claims the task when two compete."""
    task_id = "tt-pick-concurrent-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Concurrent Task", status="pending"))
        await session.commit()

    # Simulate two concurrent workers
    async with async_session_factory() as session1:
        async with async_session_factory() as session2:
            result1 = await engine._pick_pending_task(session1)
            result2 = await engine._pick_pending_task(session2)

    # Only one should have succeeded
    got_task = [r for r in [result1, result2] if r is not None]
    assert len(got_task) == 1
    assert got_task[0].id == task_id

    # Cleanup
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


# ── 3. _fail_task / _complete_task ────────────────────────────────────────


@pytest.mark.asyncio
async def test_recover_stale_tasks_resets_running_and_claimed():
    """running/claimed tasks are reset to pending; other statuses untouched."""
    running_id = "tt-recover-running-1"
    claimed_id = "tt-recover-claimed-1"
    pending_id = "tt-recover-pending-1"
    completed_id = "tt-recover-completed-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=running_id, title="Running", status="running"))
        session.add(TaskModel(id=claimed_id, title="Claimed", status="claimed"))
        session.add(TaskModel(id=pending_id, title="Pending", status="pending"))
        session.add(TaskModel(id=completed_id, title="Completed", status="completed"))
        await session.commit()

    await engine._recover_stale_tasks()

    async with async_session_factory() as session:
        running = await session.get(TaskModel, running_id)
        claimed = await session.get(TaskModel, claimed_id)
        pending = await session.get(TaskModel, pending_id)
        completed = await session.get(TaskModel, completed_id)

        assert running is not None and running.status == "pending"
        assert claimed is not None and claimed.status == "pending"
        assert pending is not None and pending.status == "pending"
        assert completed is not None and completed.status == "completed"

    # Cleanup
    async with async_session_factory() as session:
        for tid in [running_id, claimed_id, pending_id, completed_id]:
            task = await session.get(TaskModel, tid)
            if task:
                await session.delete(task)
        await session.commit()


# ── 8. _has_git_worktree_changes ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_has_git_worktree_changes_no_path():
    """No path provided → returns None."""
    result = await engine._has_git_worktree_changes(None)
    assert result is None


@pytest.mark.asyncio
async def test_has_git_worktree_changes_with_changes(monkeypatch, tmp_path):
    """subprocess returns non-empty stdout → True."""
    import asyncio

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b" M some_file.py\n", b""

    async def _fake_create_subprocess_shell(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is True


@pytest.mark.asyncio
async def test_has_git_worktree_changes_empty(monkeypatch, tmp_path):
    """subprocess returns empty stdout → False."""
    import asyncio

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def _fake_create_subprocess_shell(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is False


@pytest.mark.asyncio
async def test_has_git_worktree_changes_nonzero_returncode(monkeypatch, tmp_path):
    """subprocess returncode != 0 → None."""
    import asyncio

    class FakeProc:
        returncode = 128

        async def communicate(self):
            return b"fatal: not a git repository", b""

    async def _fake_create_subprocess_shell(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is None


@pytest.mark.asyncio
async def test_has_git_worktree_changes_exception(monkeypatch, tmp_path):
    """Exception during subprocess → None."""
    import asyncio

    async def _raising(*a, **kw):
        raise OSError("no such binary")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _raising)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is None


# ── 9. _should_skip_stage ─────────────────────────────────────────────────


def test_should_skip_stage_no_condition():
    """No condition in stage_defs → False (don't skip)."""
    stage = _make_stage(stage_name="coding")
    result = engine._should_skip_stage(stage, {}, {})  # type: ignore[arg-type]
    assert result is False


def test_should_skip_stage_condition_true(monkeypatch):
    """evaluate_condition returns True (should execute) → False (don't skip)."""
    from app.worker import conditions as cond_mod

    monkeypatch.setattr(cond_mod, "evaluate_condition", lambda cond, outputs: True)
    stage = _make_stage(stage_name="coding")
    stage_defs = {"coding": {"condition": "some_condition"}}
    result = engine._should_skip_stage(stage, stage_defs, {})  # type: ignore[arg-type]
    assert result is False


def test_should_skip_stage_condition_false(monkeypatch):
    """evaluate_condition returns False (should NOT execute) → True (skip)."""
    from app.worker import conditions as cond_mod

    monkeypatch.setattr(cond_mod, "evaluate_condition", lambda cond, outputs: False)
    stage = _make_stage(stage_name="coding")
    stage_defs = {"coding": {"condition": "some_condition"}}
    result = engine._should_skip_stage(stage, stage_defs, {})  # type: ignore[arg-type]
    assert result is True


def test_should_skip_stage_condition_raises(monkeypatch):
    """evaluate_condition raises exception → False (fallback: execute anyway)."""
    from app.worker import conditions as cond_mod

    def _raise(*a, **kw):
        raise RuntimeError("bad condition")

    monkeypatch.setattr(cond_mod, "evaluate_condition", _raise)
    stage = _make_stage(stage_name="coding")
    stage_defs = {"coding": {"condition": "some_condition"}}
    result = engine._should_skip_stage(stage, stage_defs, {})  # type: ignore[arg-type]
    assert result is False


# ── 10. _is_cancelled ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_cancelled_running():
    """task status='running' → False."""
    task_id = "tt-is-cancelled-running-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Running Task", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        result = await engine._is_cancelled(session, task_id)
    assert result is False

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_is_cancelled_cancelled():
    """task status='cancelled' → True."""
    task_id = "tt-is-cancelled-yes-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Cancelled Task", status="cancelled"))
        await session.commit()

    async with async_session_factory() as session:
        result = await engine._is_cancelled(session, task_id)
    assert result is True

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


# ── 11. _resolve_sandbox_workspace ────────────────────────────────────────


def test_resolve_sandbox_workspace_with_path(monkeypatch):
    """workspace_path provided → returns it unchanged."""
    monkeypatch.setattr(engine.settings, "SANDBOX_WORKSPACE_BASE_DIR", "/sandbox/base")
    path, source = engine._resolve_sandbox_workspace("task-42", "/custom/path", "worktree")
    assert path == "/custom/path"
    assert source == "worktree"


def test_resolve_sandbox_workspace_none_path(monkeypatch):
    """workspace_path=None → returns fallback containing task_id."""
    monkeypatch.setattr(engine.settings, "SANDBOX_WORKSPACE_BASE_DIR", "/sandbox/base")
    path, source = engine._resolve_sandbox_workspace("task-42", None, "original_source")
    assert "task-42" in path
    assert source == "fallback"


# ── 12. _parse_stage_defs and _group_stages_by_order edge cases ──────────


def test_parse_stage_defs_invalid_json():
    """Invalid JSON in template.stages → returns {}."""
    task = _make_task()
    task.template = SimpleNamespace(stages="not-valid-json{{", gates=None)
    result = engine._parse_stage_defs(task)  # type: ignore[arg-type]
    assert result == {}


def test_parse_stage_defs_no_template():
    """No template → returns {}."""
    task = _make_task()
    task.template = None
    result = engine._parse_stage_defs(task)  # type: ignore[arg-type]
    assert result == {}


def test_group_stages_by_order_no_template():
    """No template → stages each in own single-element group."""
    task = _make_task()
    task.template = None
    stages = [_make_stage(stage_name="parse"), _make_stage(stage_name="coding")]
    groups = engine._group_stages_by_order(stages, task)  # type: ignore[arg-type]
    assert len(groups) == 2
    for g in groups:
        assert len(g) == 1


# ── 13. Worker start/stop edge cases ──────────────────────────────────────


@pytest.mark.asyncio
async def test_start_worker_double_start_guard(monkeypatch):
    """start_worker when _running=True → returns early without creating a second task."""
    import app.worker.scheduler as _sched

    monkeypatch.setattr(_sched, "start_scheduler", AsyncMock())
    monkeypatch.setattr(_sched, "stop_scheduler", AsyncMock())
    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_all_agents", lambda: None)

    # Make sure worker is stopped first
    await engine.stop_worker()

    # Start once
    await engine.start_worker()
    task_after_first_start = engine._task

    # Start again — should return early, no new task created
    await engine.start_worker()
    assert engine._task is task_after_first_start  # same task object

    # Cleanup
    await engine.stop_worker()


@pytest.mark.asyncio
async def test_recover_stale_tasks_exception_does_not_raise(monkeypatch):
    """If DB error occurs in _recover_stale_tasks, it is caught internally."""
    original_factory = engine.async_session_factory

    async def _bad_context():
        raise RuntimeError("DB unavailable")

    class _BadCM:
        async def __aenter__(self):
            raise RuntimeError("DB unavailable")

        async def __aexit__(self, *a):
            pass

    monkeypatch.setattr(engine, "async_session_factory", lambda: _BadCM())
    # Should not raise
    await engine._recover_stale_tasks()
    # Restore
    monkeypatch.setattr(engine, "async_session_factory", original_factory)


@pytest.mark.asyncio
async def test_prune_stale_worktrees_disabled(monkeypatch):
    """_prune_stale_worktrees returns immediately when WORKTREE_ENABLED=False."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    # Should complete immediately without touching DB
    await engine._prune_stale_worktrees()  # no assertion needed — just must not raise


@pytest.mark.asyncio
async def test_prune_stale_worktrees_db_error(monkeypatch):
    """_prune_stale_worktrees with WORKTREE_ENABLED=True but DB error → logs, returns."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    class _BadCM:
        async def __aenter__(self):
            raise RuntimeError("DB unavailable")

        async def __aexit__(self, *a):
            pass

    original_factory = engine.async_session_factory
    monkeypatch.setattr(engine, "async_session_factory", lambda: _BadCM())
    # Should not raise
    await engine._prune_stale_worktrees()
    monkeypatch.setattr(engine, "async_session_factory", original_factory)


# ── 14. _cleanup_runtime_resources paths ──────────────────────────────────
# Section 20: _prune_stale_worktrees
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prune_stale_worktrees_with_repo_local_path(monkeypatch):
    """WORKTREE_ENABLED=True, project with repo_local_path → prune called."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    cleaned_calls = []

    class FakeMgr:
        async def prune_all_stale(self):
            cleaned_calls.append(1)
            return 2

    rows = [(str("proj-1"), "/some/repo/path", None)]

    class FakeResult:
        def all(self):
            return rows

    fake_session = SimpleNamespace(
        execute=AsyncMock(return_value=FakeResult()),
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_session_factory():
        yield fake_session

    monkeypatch.setattr(engine, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(engine, "get_managed_repo_path", lambda pid, url: type("P", (), {"__truediv__": lambda s, x: type("F", (), {"exists": lambda: False})()})())
    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    await engine._prune_stale_worktrees()
    assert len(cleaned_calls) == 1


@pytest.mark.asyncio
async def test_prune_stale_worktrees_exception(monkeypatch):
    """WORKTREE_ENABLED=True, session.execute raises → exception swallowed."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    import contextlib

    @contextlib.asynccontextmanager
    async def _fail_session():
        raise RuntimeError("DB down")
        yield  # pragma: no cover

    monkeypatch.setattr(engine, "async_session_factory", _fail_session)
    await engine._prune_stale_worktrees()  # must not raise


@pytest.mark.asyncio
async def test_prune_stale_worktrees_with_repo_url_and_managed_path(monkeypatch):
    """WORKTREE_ENABLED=True, project with repo_url and managed path that exists."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    import contextlib

    cleaned_calls = []

    class FakeMgr:
        async def prune_all_stale(self):
            cleaned_calls.append(1)
            return 0

    rows = [(str("proj-2"), None, "https://github.com/org/repo.git")]

    class FakeResult:
        def all(self):
            return rows

    fake_session = SimpleNamespace(execute=AsyncMock(return_value=FakeResult()))

    @contextlib.asynccontextmanager
    async def _fake_session_factory():
        yield fake_session

    fake_managed = SimpleNamespace(__truediv__=lambda self, x: type("GitDir", (), {"exists": lambda: True})())

    monkeypatch.setattr(engine, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(engine, "get_managed_repo_path", lambda pid, url: fake_managed)
    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    await engine._prune_stale_worktrees()


# ═══════════════════════════════════════════════════════════════════════
# Section 21: _pick_pending_task rowcount==0
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pick_pending_task_race_condition():
    """Two workers compete — second UPDATE returns rowcount==0 → returns None."""
    task_id = "tt-race-task-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Race Task", status="pending"))
        await session.commit()

    # First worker claims it
    async with async_session_factory() as session1:
        task = await engine._pick_pending_task(session1)
        assert task is not None
        assert task.id == task_id

    # Second worker now sees no pending tasks
    async with async_session_factory() as session2:
        task2 = await engine._pick_pending_task(session2)
        assert task2 is None

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 30: _sort_stages and _group_stages_by_order JSON decode
# ═══════════════════════════════════════════════════════════════════════

def test_sort_stages_invalid_json():
    """template.stages is invalid JSON → returns unsorted list."""
    stage1 = SimpleNamespace(stage_name="coding", id="s1")
    stage2 = SimpleNamespace(stage_name="review", id="s2")
    task = SimpleNamespace(
        template=SimpleNamespace(stages="not-valid-json{"),
        stages=[stage1, stage2],
    )
    result = engine._sort_stages(task)
    assert len(result) == 2


def test_group_stages_invalid_json():
    """template.stages is invalid JSON → each stage in its own group."""
    stage1 = SimpleNamespace(stage_name="coding", id="s1")
    stage2 = SimpleNamespace(stage_name="review", id="s2")
    task = SimpleNamespace(
        template=SimpleNamespace(stages="{{bad-json"),
    )
    result = engine._group_stages_by_order([stage1, stage2], task)
    assert len(result) == 2
    assert result[0] == [stage1]
    assert result[1] == [stage2]


# ═══════════════════════════════════════════════════════════════════════
