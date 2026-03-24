"""Tests for app.worker.workspace_manager module."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker import workspace_manager as wm


# ---------------------------------------------------------------------------
# _has_git_worktree_changes
# ---------------------------------------------------------------------------


class TestHasGitWorktreeChanges:
    @pytest.mark.asyncio
    async def test_none_path_returns_none(self):
        result = await wm._has_git_worktree_changes(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_path_returns_none(self):
        result = await wm._has_git_worktree_changes("")
        assert result is None

    @pytest.mark.asyncio
    async def test_has_changes(self, monkeypatch):
        class FakeProc:
            returncode = 0
            async def communicate(self):
                return b" M file.py\n", b""

        async def fake_subprocess(*a, **kw):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_subprocess)
        result = await wm._has_git_worktree_changes("/tmp/ws")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_changes(self, monkeypatch):
        class FakeProc:
            returncode = 0
            async def communicate(self):
                return b"", b""

        async def fake_subprocess(*a, **kw):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_subprocess)
        result = await wm._has_git_worktree_changes("/tmp/ws")
        assert result is False

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_none(self, monkeypatch):
        class FakeProc:
            returncode = 128
            async def communicate(self):
                return b"", b"fatal: not a git repo"

        async def fake_subprocess(*a, **kw):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_subprocess)
        result = await wm._has_git_worktree_changes("/tmp/ws")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, monkeypatch):
        async def fake_subprocess(*a, **kw):
            raise OSError("no such dir")

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_subprocess)
        result = await wm._has_git_worktree_changes("/tmp/ws")
        assert result is None


# ---------------------------------------------------------------------------
# _ensure_code_stage_has_changes
# ---------------------------------------------------------------------------


class TestEnsureCodeStageHasChanges:
    @pytest.mark.asyncio
    async def test_non_code_stage_returns_true(self, monkeypatch):
        fake_engine = SimpleNamespace(
            _fail_task=AsyncMock(),
            _has_git_worktree_changes=AsyncMock(),
            mark_stage_failed=AsyncMock(),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        session = SimpleNamespace()
        task = SimpleNamespace(id="t-1")
        stage = SimpleNamespace(stage_name="review")
        result = await wm._ensure_code_stage_has_changes(session, task, stage, "/ws")
        assert result is True

    @pytest.mark.asyncio
    async def test_code_stage_no_worktree_returns_true(self, monkeypatch):
        fake_engine = SimpleNamespace(
            _fail_task=AsyncMock(),
            _has_git_worktree_changes=AsyncMock(),
            mark_stage_failed=AsyncMock(),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        session = SimpleNamespace()
        task = SimpleNamespace(id="t-1")
        stage = SimpleNamespace(stage_name="code")
        result = await wm._ensure_code_stage_has_changes(session, task, stage, None)
        assert result is True

    @pytest.mark.asyncio
    async def test_code_stage_with_changes_returns_true(self, monkeypatch):
        fake_engine = SimpleNamespace(
            _fail_task=AsyncMock(),
            _has_git_worktree_changes=AsyncMock(return_value=True),
            mark_stage_failed=AsyncMock(),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        session = SimpleNamespace()
        task = SimpleNamespace(id="t-1")
        stage = SimpleNamespace(stage_name="code")
        result = await wm._ensure_code_stage_has_changes(session, task, stage, "/ws")
        assert result is True

    @pytest.mark.asyncio
    async def test_code_stage_no_changes_fails_task(self, monkeypatch):
        fail_task_mock = AsyncMock()
        mark_stage_mock = AsyncMock()
        fake_engine = SimpleNamespace(
            _fail_task=fail_task_mock,
            _has_git_worktree_changes=AsyncMock(return_value=False),
            mark_stage_failed=mark_stage_mock,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setitem(
            sys.modules, "app.worker.agents",
            SimpleNamespace(close_agents_for_task=MagicMock()),
        )

        session = SimpleNamespace()
        task = SimpleNamespace(id="t-1")
        stage = SimpleNamespace(stage_name="code")
        result = await wm._ensure_code_stage_has_changes(session, task, stage, "/ws")
        assert result is False
        mark_stage_mock.assert_awaited_once()
        fail_task_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# _setup_worktree
# ---------------------------------------------------------------------------


class TestSetupWorktree:
    @pytest.mark.asyncio
    async def test_worktree_disabled(self, monkeypatch):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", False)
        task = SimpleNamespace(project=None)
        path, mgr = await wm._setup_worktree(task)
        assert path is None and mgr is None

    @pytest.mark.asyncio
    async def test_no_project(self, monkeypatch):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)
        task = SimpleNamespace(project=None)
        path, mgr = await wm._setup_worktree(task)
        assert path is None and mgr is None

    @pytest.mark.asyncio
    async def test_no_repo_local_path_no_repo_url(self, monkeypatch):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)
        fake_engine = SimpleNamespace(
            Path=Path,
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            ensure_repo_local_mirror=AsyncMock(return_value=""),
            get_worktree_manager=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        project = SimpleNamespace(
            id="p-1", repo_local_path="", repo_url="", branch="main",
            sandbox_image=None,
        )
        task = SimpleNamespace(id="t-1", project=project, title="T", target_branch=None)
        path, mgr = await wm._setup_worktree(task)
        assert path is None and mgr is None

    @pytest.mark.asyncio
    async def test_success_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)

        fake_worktree_mgr = SimpleNamespace(
            create_worktree=AsyncMock(return_value="/tmp/wt/t-1"),
        )
        fake_engine = SimpleNamespace(
            Path=Path,
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            ensure_repo_local_mirror=AsyncMock(),
            get_worktree_manager=MagicMock(return_value=fake_worktree_mgr),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        project = SimpleNamespace(
            id="p-1", repo_local_path=str(repo_dir), repo_url="https://github.com/x/y",
            branch="main", sandbox_image=None,
        )
        task = SimpleNamespace(id="t-1", project=project, title="T", target_branch=None)
        path, mgr = await wm._setup_worktree(task)
        assert path == "/tmp/wt/t-1"
        assert mgr is fake_worktree_mgr

    @pytest.mark.asyncio
    async def test_worktree_creation_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)

        fake_worktree_mgr = SimpleNamespace(
            create_worktree=AsyncMock(return_value=None),
        )
        fake_engine = SimpleNamespace(
            Path=Path,
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            ensure_repo_local_mirror=AsyncMock(),
            get_worktree_manager=MagicMock(return_value=fake_worktree_mgr),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        project = SimpleNamespace(
            id="p-1", repo_local_path=str(repo_dir), repo_url="https://github.com/x/y",
            branch="main", sandbox_image=None,
        )
        task = SimpleNamespace(id="t-1", project=project, title="T", target_branch=None)
        path, mgr = await wm._setup_worktree(task)
        assert path is None and mgr is None

    @pytest.mark.asyncio
    async def test_worktree_creation_exception(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)

        fake_worktree_mgr = SimpleNamespace(
            create_worktree=AsyncMock(side_effect=RuntimeError("boom")),
        )
        fake_engine = SimpleNamespace(
            Path=Path,
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            ensure_repo_local_mirror=AsyncMock(),
            get_worktree_manager=MagicMock(return_value=fake_worktree_mgr),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        project = SimpleNamespace(
            id="p-1", repo_local_path=str(repo_dir), repo_url="https://github.com/x/y",
            branch="main", sandbox_image=None,
        )
        task = SimpleNamespace(id="t-1", project=project, title="T", target_branch=None)
        path, mgr = await wm._setup_worktree(task)
        # Exception path: worktree_path stays None but mgr is returned
        assert path is None


# ---------------------------------------------------------------------------
# _prepare_runtime_workspace
# ---------------------------------------------------------------------------


class TestPrepareRuntimeWorkspace:
    @pytest.mark.asyncio
    async def test_worktree_path_passthrough(self, monkeypatch):
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path, prepare_workspace_from_repo=AsyncMock(), tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        task = SimpleNamespace(id="t-1", project=None, target_branch="feat/x")
        ws, source, branch = await wm._prepare_runtime_workspace(task, "/wt/path")
        assert ws == "/wt/path"
        assert source == "worktree"
        assert branch == "feat/x"

    @pytest.mark.asyncio
    async def test_no_project_returns_tmp_empty(self, monkeypatch, tmp_path):
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path, prepare_workspace_from_repo=AsyncMock(), tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", False)

        task = SimpleNamespace(id="t-1", project=None, target_branch=None)
        ws, source, branch = await wm._prepare_runtime_workspace(task, None)
        assert source == "tmp_empty"
        assert ws is not None

    @pytest.mark.asyncio
    async def test_worktree_enabled_with_repo_url_returns_required(self, monkeypatch):
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path, prepare_workspace_from_repo=AsyncMock(), tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)

        project = SimpleNamespace(repo_url="https://github.com/x/y", branch="main")
        task = SimpleNamespace(id="t-1", project=project, target_branch=None)
        ws, source, branch = await wm._prepare_runtime_workspace(task, None)
        assert source == "worktree_required"
        assert ws is None

    @pytest.mark.asyncio
    async def test_clone_success(self, monkeypatch):
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path,
            prepare_workspace_from_repo=AsyncMock(return_value=(True, "feat/x", None)),
            tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", False)

        project = SimpleNamespace(repo_url="https://github.com/x/y", branch="main")
        task = SimpleNamespace(id="t-1", project=project, target_branch=None, title="T")
        ws, source, branch = await wm._prepare_runtime_workspace(task, None)
        assert source == "tmp_cloned"
        assert branch == "feat/x"

    @pytest.mark.asyncio
    async def test_clone_failure(self, monkeypatch):
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path,
            prepare_workspace_from_repo=AsyncMock(return_value=(False, None, "clone failed")),
            tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", False)

        project = SimpleNamespace(repo_url="https://github.com/x/y", branch="main")
        task = SimpleNamespace(id="t-1", project=project, target_branch=None, title="T")
        ws, source, branch = await wm._prepare_runtime_workspace(task, None)
        assert source == "tmp_clone_failed"
        assert ws is None


# ---------------------------------------------------------------------------
# _build_repo_context
# ---------------------------------------------------------------------------


class TestBuildRepoContext:
    def test_full_context(self):
        project = SimpleNamespace(
            tech_stack=["Python", "React"],
            repo_tree="src/\n  main.py",
            repo_url="https://github.com/x/y",
            branch="main",
        )
        result = wm._build_repo_context(project)
        assert "Python" in result
        assert "src/" in result
        assert "github.com" in result

    def test_no_fields(self):
        project = SimpleNamespace(
            tech_stack=None, repo_tree=None, repo_url=None, branch=None,
        )
        result = wm._build_repo_context(project)
        assert result == ""

    def test_repo_tree_truncation(self):
        project = SimpleNamespace(
            tech_stack=None,
            repo_tree="x" * 5000,
            repo_url=None,
            branch=None,
        )
        result = wm._build_repo_context(project)
        assert "已截断" in result
        assert len(result) < 5000


# ---------------------------------------------------------------------------
# _cleanup_runtime_resources
# ---------------------------------------------------------------------------


class TestCleanupRuntimeResources:
    @pytest.mark.asyncio
    async def test_noop_when_all_none(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        task = SimpleNamespace(id="t-1")
        await wm._cleanup_runtime_resources(task, None, None, None, "none", None, None)

    @pytest.mark.asyncio
    async def test_worktree_cleanup(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        cleanup_mock = AsyncMock()
        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        worktree_mgr = SimpleNamespace(cleanup_worktree=cleanup_mock)
        task = SimpleNamespace(id="t-1")
        await wm._cleanup_runtime_resources(
            task, worktree_mgr, "/wt/path", None, "worktree", None, None,
        )
        cleanup_mock.assert_awaited_once_with("t-1")

    @pytest.mark.asyncio
    async def test_sandbox_destroy_role_sandboxes(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        destroy_mock = AsyncMock()
        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        sandbox_mgr = SimpleNamespace(destroy_role_sandboxes=destroy_mock)
        task = SimpleNamespace(id="t-1")
        await wm._cleanup_runtime_resources(
            task, None, None, None, "none", sandbox_mgr, None,
        )
        destroy_mock.assert_awaited_once_with("t-1")

    @pytest.mark.asyncio
    async def test_sandbox_destroy_fallback(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        destroy_mock = AsyncMock()
        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        sandbox_mgr = SimpleNamespace(destroy=destroy_mock)
        sandbox_info = SimpleNamespace(container_name="c-1")
        task = SimpleNamespace(id="t-1")
        await wm._cleanup_runtime_resources(
            task, None, None, None, "none", sandbox_mgr, sandbox_info,
        )
        destroy_mock.assert_awaited_once_with("t-1")


# ---------------------------------------------------------------------------
# _finalize_task_resources (basic paths)
# ---------------------------------------------------------------------------


class TestFinalizeTaskResources:
    @pytest.mark.asyncio
    async def test_no_repo_url_skips_scm(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=AsyncMock(),
            create_pr_for_workspace=AsyncMock(),
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", False)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", False)

        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1", project=SimpleNamespace(repo_url="", branch="main"),
            title="T", target_branch=None, branch_name=None, pr_url=None,
            project_id="p-1",
        )
        result = await wm._finalize_task_resources(
            session, task, [], None, None, None, None, "none", None, None, None,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_memory_extraction_enabled(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        extract_mock = AsyncMock()
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=AsyncMock(),
            create_pr_for_workspace=AsyncMock(),
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setitem(
            sys.modules, "app.worker.memory_extractor",
            SimpleNamespace(extract_and_store_memories=extract_mock),
        )
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", True)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", False)

        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1", project=SimpleNamespace(repo_url="", branch="main"),
            title="T", target_branch=None, branch_name=None, pr_url=None,
            project_id="p-1",
        )
        prior_outputs = [{"role": "coding", "output": "done"}]
        memory_store = SimpleNamespace()
        result = await wm._finalize_task_resources(
            session, task, prior_outputs, memory_store,
            None, None, None, "none", None, None, None,
        )
        assert result is True
        extract_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_memory_extraction_failure_continues(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        extract_mock = AsyncMock(side_effect=RuntimeError("llm error"))
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=AsyncMock(),
            create_pr_for_workspace=AsyncMock(),
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setitem(
            sys.modules, "app.worker.memory_extractor",
            SimpleNamespace(extract_and_store_memories=extract_mock),
        )
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", True)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", False)

        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1", project=SimpleNamespace(repo_url="", branch="main"),
            title="T", target_branch=None, branch_name=None, pr_url=None,
            project_id="p-1",
        )
        result = await wm._finalize_task_resources(
            session, task, [{"role": "coding", "output": "done"}], SimpleNamespace(),
            None, None, None, "none", None, None, None,
        )
        assert result is True  # should continue despite memory error

    @pytest.mark.asyncio
    async def test_skill_feedback_enabled(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        agg_mock = AsyncMock()
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=AsyncMock(),
            create_pr_for_workspace=AsyncMock(),
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setitem(
            sys.modules, "app.services.skill_feedback_service",
            SimpleNamespace(aggregate_skill_metrics=agg_mock),
        )
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", False)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", True)

        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1", project=SimpleNamespace(repo_url="", branch="main"),
            title="T", target_branch=None, branch_name=None, pr_url=None,
            project_id="p-1",
        )
        result = await wm._finalize_task_resources(
            session, task, [], None, None, None, None, "none", None, None, None,
        )
        assert result is True
        agg_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scm_commit_push_with_worktree(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        commit_mock = AsyncMock(return_value="feat/branch-1")
        pr_mock = AsyncMock(return_value="https://github.com/x/y/pull/1")
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=AsyncMock(),
            create_pr_for_workspace=AsyncMock(),
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", False)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", False)

        worktree_mgr = SimpleNamespace(
            commit_and_push=commit_mock,
            create_pr=pr_mock,
            cleanup_worktree=AsyncMock(),
        )
        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1",
            project=SimpleNamespace(repo_url="https://github.com/x/y", branch="main"),
            title="T", target_branch=None, branch_name=None, pr_url=None,
            project_id="p-1", github_issue_number=42,
        )
        result = await wm._finalize_task_resources(
            session, task, [], None,
            worktree_mgr, "/wt/path", "/wt/path", "worktree", None, None, None,
        )
        assert result is True
        commit_mock.assert_awaited_once()
        pr_mock.assert_awaited_once()
        assert task.branch_name == "feat/branch-1"
        assert task.pr_url == "https://github.com/x/y/pull/1"

    @pytest.mark.asyncio
    async def test_scm_fallback_workspace_commit_push(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        commit_mock = AsyncMock(return_value="feat/branch-2")
        pr_mock = AsyncMock(return_value=None)
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=commit_mock,
            create_pr_for_workspace=pr_mock,
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", False)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", False)

        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1",
            project=SimpleNamespace(repo_url="https://github.com/x/y", branch="main"),
            title="T", target_branch="feat/x", branch_name=None, pr_url=None,
            project_id="p-1", github_issue_number=None,
        )
        result = await wm._finalize_task_resources(
            session, task, [], None,
            None, None, "/tmp/ws", "tmp_cloned", "feat/x", None, None,
        )
        assert result is True
        commit_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scm_commit_push_failure_returns_false(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            _fail_task=AsyncMock(),
            commit_and_push_workspace=AsyncMock(side_effect=RuntimeError("push failed")),
            create_pr_for_workspace=AsyncMock(),
            Path=Path, shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)
        monkeypatch.setattr(wm.settings, "MEMORY_ENABLED", False)
        monkeypatch.setattr(wm.settings, "SKILL_FEEDBACK_ENABLED", False)

        session = SimpleNamespace(commit=AsyncMock())
        task = SimpleNamespace(
            id="t-1",
            project=SimpleNamespace(repo_url="https://github.com/x/y", branch="main"),
            title="T", target_branch=None, branch_name=None, pr_url=None,
            project_id="p-1", github_issue_number=None,
        )
        result = await wm._finalize_task_resources(
            session, task, [], None,
            None, None, "/tmp/ws", "tmp_cloned", None, None, None,
        )
        assert result is False


# ---------------------------------------------------------------------------
# _setup_worktree — additional paths
# ---------------------------------------------------------------------------


class TestSetupWorktreeAdditional:
    @pytest.mark.asyncio
    async def test_invalid_repo_local_path_auto_clones(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)

        fake_worktree_mgr = SimpleNamespace(
            create_worktree=AsyncMock(return_value="/tmp/wt/t-1"),
        )
        mirror_mock = AsyncMock(return_value=str(tmp_path / "mirror"))
        (tmp_path / "mirror").mkdir()
        fake_engine = SimpleNamespace(
            Path=Path,
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            ensure_repo_local_mirror=mirror_mock,
            get_worktree_manager=MagicMock(return_value=fake_worktree_mgr),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        project = SimpleNamespace(
            id="p-1",
            repo_local_path="/nonexistent/path",
            repo_url="https://github.com/x/y",
            branch="main",
            sandbox_image=None,
        )
        task = SimpleNamespace(id="t-1", project=project, title="T", target_branch=None)
        path, mgr = await wm._setup_worktree(task)
        assert path == "/tmp/wt/t-1"
        mirror_mock.assert_awaited_once()
        assert project.repo_local_path == str(tmp_path / "mirror")

    @pytest.mark.asyncio
    async def test_auto_clone_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(wm.settings, "WORKTREE_ENABLED", True)

        mirror_mock = AsyncMock(return_value="")
        fake_engine = SimpleNamespace(
            Path=Path,
            _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(),
            ensure_repo_local_mirror=mirror_mock,
            get_worktree_manager=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        project = SimpleNamespace(
            id="p-1", repo_local_path="", repo_url="https://github.com/x/y",
            branch="main", sandbox_image=None,
        )
        task = SimpleNamespace(id="t-1", project=project, title="T", target_branch=None)
        path, mgr = await wm._setup_worktree(task)
        assert path is None and mgr is None


# ---------------------------------------------------------------------------
# _cleanup_runtime_resources — additional paths
# ---------------------------------------------------------------------------


class TestCleanupRuntimeResourcesAdditional:
    @pytest.mark.asyncio
    async def test_worktree_cleanup_failure_swallowed(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        worktree_mgr = SimpleNamespace(
            cleanup_worktree=AsyncMock(side_effect=RuntimeError("cleanup failed")),
        )
        task = SimpleNamespace(id="t-1")
        # Should not raise
        await wm._cleanup_runtime_resources(
            task, worktree_mgr, "/wt/path", None, "worktree", None, None,
        )

    @pytest.mark.asyncio
    async def test_tmp_workspace_cleanup(self, monkeypatch, tmp_path):
        import shutil as _shutil
        import tempfile as _tempfile

        # Create a tmp workspace under the expected silicon_agent/tasks path
        tasks_dir = Path(_tempfile.gettempdir()) / "silicon_agent" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_ws = tasks_dir / "t-cleanup"
        task_ws.mkdir(exist_ok=True)
        (task_ws / "file.txt").write_text("test")

        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        task = SimpleNamespace(id="t-cleanup")
        await wm._cleanup_runtime_resources(
            task, None, None, str(task_ws), "tmp_cloned", None, None,
        )
        assert not task_ws.exists()

    @pytest.mark.asyncio
    async def test_sandbox_destroy_exception_swallowed(self, monkeypatch):
        import shutil as _shutil
        import tempfile as _tempfile
        fake_engine = SimpleNamespace(
            Path=Path, _emit_system_log=AsyncMock(return_value="log-1"),
            _close_started_system_log=AsyncMock(), shutil=_shutil, tempfile=_tempfile,
        )
        monkeypatch.setitem(sys.modules, "app.worker.engine", fake_engine)

        sandbox_mgr = SimpleNamespace(
            destroy_role_sandboxes=AsyncMock(side_effect=RuntimeError("boom")),
        )
        task = SimpleNamespace(id="t-1")
        # Should not raise
        await wm._cleanup_runtime_resources(
            task, None, None, None, "none", sandbox_mgr, None,
        )
