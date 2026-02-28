from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import engine


@pytest.mark.asyncio
async def test_setup_worktree_auto_clones_when_repo_local_path_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    task = SimpleNamespace(
        id="task-1",
        title="auto clone setup",
        target_branch="task/auto-clone",
        project=SimpleNamespace(
            id="project-1",
            repo_local_path=None,
            repo_url="https://example.com/org/repo.git",
            branch="stg",
        ),
    )
    fake_mgr = SimpleNamespace(create_worktree=AsyncMock(return_value="/tmp/wt/task-1"))
    captured_repo_paths: list[str] = []
    emitted: list[dict] = []

    async def _fake_emit(_task, **kwargs):
        emitted.append(kwargs)
        return "log-started"

    def _fake_get_mgr(repo_path: str):
        captured_repo_paths.append(repo_path)
        return fake_mgr

    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(
        engine,
        "ensure_repo_local_mirror",
        AsyncMock(return_value="/tmp/repos/project-1"),
    )
    monkeypatch.setattr(engine, "get_worktree_manager", _fake_get_mgr)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(side_effect=_fake_emit))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    worktree_path, worktree_mgr = await engine._setup_worktree(task)  # type: ignore[arg-type]
    assert worktree_path == "/tmp/wt/task-1"
    assert worktree_mgr is fake_mgr
    assert captured_repo_paths == ["/tmp/repos/project-1"]
    assert task.project.repo_local_path == "/tmp/repos/project-1"
    assert emitted[0]["response_body"]["repo_path_source"] == "auto_cloned"


@pytest.mark.asyncio
async def test_setup_worktree_returns_none_when_auto_clone_fails(monkeypatch: pytest.MonkeyPatch):
    task = SimpleNamespace(
        id="task-2",
        title="auto clone failed",
        target_branch="task/auto-clone-failed",
        project=SimpleNamespace(
            id="project-2",
            repo_local_path=None,
            repo_url="https://example.com/org/repo.git",
            branch="main",
        ),
    )

    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine, "ensure_repo_local_mirror", AsyncMock(return_value=None))
    emit = AsyncMock()
    monkeypatch.setattr(engine, "_emit_system_log", emit)

    worktree_path, worktree_mgr = await engine._setup_worktree(task)  # type: ignore[arg-type]
    assert worktree_path is None
    assert worktree_mgr is None
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_worktree_clears_invalid_repo_local_path(monkeypatch: pytest.MonkeyPatch):
    task = SimpleNamespace(
        id="task-3",
        title="repair invalid local path",
        target_branch="task/repair-path",
        project=SimpleNamespace(
            id="project-3",
            repo_local_path="/tmp/not-exists/repo",
            repo_url="https://example.com/org/repo.git",
            branch="main",
        ),
    )
    fake_mgr = SimpleNamespace(create_worktree=AsyncMock(return_value="/tmp/wt/task-3"))
    emitted: list[dict] = []

    async def _fake_emit(_task, **kwargs):
        emitted.append(kwargs)
        return "log-started"

    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine.Path, "exists", lambda _self: False)
    monkeypatch.setattr(
        engine,
        "ensure_repo_local_mirror",
        AsyncMock(return_value="/tmp/repos/project-3"),
    )
    monkeypatch.setattr(engine, "get_worktree_manager", lambda _repo_path: fake_mgr)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(side_effect=_fake_emit))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    worktree_path, _ = await engine._setup_worktree(task)  # type: ignore[arg-type]
    assert worktree_path == "/tmp/wt/task-3"
    assert task.project.repo_local_path == "/tmp/repos/project-3"
    assert emitted[0]["response_body"]["repo_path_source"] == "invalid_config_repaired"


@pytest.mark.asyncio
async def test_setup_worktree_emits_failed_when_manager_returns_none(
    monkeypatch: pytest.MonkeyPatch,
):
    task = SimpleNamespace(
        id="task-4",
        title="worktree unavailable",
        target_branch="task/not-found",
        project=SimpleNamespace(
            id="project-4",
            repo_local_path="/tmp/repos/project-4",
            repo_url="https://example.com/org/repo.git",
            branch="main",
        ),
    )
    fake_mgr = SimpleNamespace(create_worktree=AsyncMock(return_value=None))
    emitted: list[dict] = []
    close_started = AsyncMock()

    async def _fake_emit(_task, **kwargs):
        emitted.append(kwargs)
        return "log-started"

    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine.Path, "exists", lambda _self: True)
    monkeypatch.setattr(engine, "get_worktree_manager", lambda _repo_path: fake_mgr)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(side_effect=_fake_emit))
    monkeypatch.setattr(engine, "_close_started_system_log", close_started)

    worktree_path, worktree_mgr = await engine._setup_worktree(task)  # type: ignore[arg-type]
    assert worktree_path is None
    assert worktree_mgr is None
    assert emitted[1]["event_type"] == "worktree_create_finished"
    assert emitted[1]["status"] == "failed"
    assert emitted[1]["response_body"]["error"] == "worktree_path_unavailable"
    close_started.assert_awaited_once()
    assert close_started.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_requires_worktree_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    task = SimpleNamespace(
        id="task-5",
        title="requires worktree",
        target_branch="task/requires-worktree",
        project=SimpleNamespace(
            repo_url="https://example.com/org/repo.git",
            branch="main",
        ),
    )
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    workspace_path, source, branch = await engine._prepare_runtime_workspace(  # type: ignore[arg-type]
        task,
        worktree_path=None,
    )
    assert workspace_path is None
    assert source == "worktree_required"
    assert branch is None


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_clones_tmp_repo_when_worktree_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    task = SimpleNamespace(
        id="task-6",
        title="tmp clone",
        target_branch=None,
        project=SimpleNamespace(
            repo_url="https://example.com/org/repo.git",
            branch="release",
            repo_local_path="/unused/local/path",
        ),
    )
    prepare = AsyncMock(return_value=(True, "task/generated-branch", None))

    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    monkeypatch.setattr(engine, "prepare_workspace_from_repo", prepare)

    workspace_path, source, branch = await engine._prepare_runtime_workspace(  # type: ignore[arg-type]
        task,
        worktree_path=None,
    )
    assert workspace_path is not None
    assert source == "tmp_cloned"
    assert branch == "task/generated-branch"
    prepare.assert_awaited_once()
    assert prepare.await_args.kwargs["repo_url"] == "https://example.com/org/repo.git"
    assert prepare.await_args.kwargs["base_branch"] == "release"
