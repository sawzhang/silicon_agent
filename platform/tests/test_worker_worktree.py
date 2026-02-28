from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.worker import worktree


@pytest.mark.asyncio
async def test_run_with_retry_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    sleeps: list[float] = []

    async def _fake_run(cmd: str, cwd=None):
        calls.append(cmd)
        if len(calls) == 1:
            return 1, "", "temporary error"
        return 0, "ok", ""

    async def _fake_sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr(worktree, "_run", _fake_run)
    monkeypatch.setattr(worktree.asyncio, "sleep", _fake_sleep)

    rc, out, err = await worktree._run_with_retry("git status", max_retries=3, base_delay=0.25)
    assert (rc, out, err) == (0, "ok", "")
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_sanitize_branch_name():
    branch = worktree._sanitize_branch_name("12345678-aaaa", "Fix payment callback! now")
    assert branch.startswith("task/12345678-")
    assert "Fix-payment-callback-now" in branch


def test_get_managed_repo_path_uses_project_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "WORKTREE_REPO_CACHE_DIR", str(tmp_path / "repos"))
    path = worktree.get_managed_repo_path("proj-123", "https://example.com/org/repo.git")
    assert path == tmp_path / "repos" / "proj-123"


def test_get_managed_repo_path_defaults_to_worktree_base_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(tmp_path / "wt"))
    monkeypatch.setattr(worktree.settings, "WORKTREE_REPO_CACHE_DIR", "")
    path = worktree.get_managed_repo_path("proj-xyz", "https://example.com/org/repo.git")
    assert path == tmp_path / "wt" / "repos" / "proj-xyz"


def test_resolve_git_token_by_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "GITHUB_TOKEN", "gh-token")
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "https://scm.example.com/api/v3")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "ghe-token")

    github_token = worktree._resolve_git_token_for_repo_url("https://github.com/org/repo.git")
    ghe_token = worktree._resolve_git_token_for_repo_url("https://scm.example.com/china/repo.git")
    assert github_token == "gh-token"
    assert ghe_token == "ghe-token"


def test_inject_git_auth_adds_basic_header_for_https(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "https://scm.example.com/api/v3")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "token-china")
    cmd = worktree._inject_git_auth(
        "git fetch --prune origin",
        "https://scm.example.com/china/my/repo.git",
    )
    assert cmd.startswith("git -c http.extraheader=")
    assert "fetch --prune origin" in cmd
    assert "Basic" in cmd
    assert "eC1hY2Nlc3MtdG9rZW46dG9rZW4tY2hpbmE=" in cmd


def test_inject_git_auth_skips_for_ssh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "token-china")
    cmd = worktree._inject_git_auth(
        "git fetch --prune origin",
        "git@scm.example.com:china/my/repo.git",
    )
    assert cmd == "git fetch --prune origin"


@pytest.mark.asyncio
async def test_ensure_repo_local_mirror_clone_and_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "WORKTREE_REPO_CACHE_DIR", str(tmp_path / "repos"))
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "")
    monkeypatch.setattr(worktree.settings, "GITHUB_TOKEN", "")
    worktree._repo_locks.clear()
    repo_url = "https://example.com/org/repo.git"
    repo_path = worktree.get_managed_repo_path("proj-1", repo_url)
    retry_calls: list[str] = []
    run_calls: list[str] = []

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        retry_calls.append(cmd)
        if cmd.startswith("git clone"):
            (repo_path / ".git").mkdir(parents=True, exist_ok=True)
        return 0, "", ""

    async def _fake_run(cmd: str, cwd=None):
        run_calls.append(cmd)
        if cmd.startswith("git rev-parse --verify --quiet"):
            return 0, "", ""
        if cmd.startswith("git checkout -B"):
            return 0, "", ""
        return 1, "", "unexpected"

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)
    monkeypatch.setattr(worktree, "_run", _fake_run)

    resolved = await worktree.ensure_repo_local_mirror("proj-1", repo_url, base_branch="stg")
    assert resolved == str(repo_path)
    assert any(c.startswith("git clone --origin origin") for c in retry_calls)
    assert any(c == "git fetch --prune origin" for c in retry_calls)
    assert any(c.startswith("git checkout -B") for c in run_calls)


@pytest.mark.asyncio
async def test_ensure_repo_local_mirror_reclone_when_remote_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(worktree.settings, "WORKTREE_REPO_CACHE_DIR", str(tmp_path / "repos"))
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "")
    monkeypatch.setattr(worktree.settings, "GITHUB_TOKEN", "")
    worktree._repo_locks.clear()
    repo_url = "https://example.com/org/repo.git"
    repo_path = worktree.get_managed_repo_path("proj-2", repo_url)
    (repo_path / ".git").mkdir(parents=True, exist_ok=True)
    retry_calls: list[str] = []

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        retry_calls.append(cmd)
        if cmd.startswith("git clone"):
            (repo_path / ".git").mkdir(parents=True, exist_ok=True)
        return 0, "", ""

    async def _fake_run(cmd: str, cwd=None):
        if cmd == "git remote get-url origin":
            return 0, "https://example.com/other/repo.git", ""
        if cmd.startswith("git rev-parse --verify --quiet"):
            return 0, "", ""
        if cmd.startswith("git checkout -B"):
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)
    monkeypatch.setattr(worktree, "_run", _fake_run)

    resolved = await worktree.ensure_repo_local_mirror("proj-2", repo_url, base_branch="main")
    assert resolved == str(repo_path)
    assert any(c.startswith("git clone --origin origin") for c in retry_calls)


@pytest.mark.asyncio
async def test_ensure_repo_local_mirror_branch_missing_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(worktree.settings, "WORKTREE_REPO_CACHE_DIR", str(tmp_path / "repos"))
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "")
    monkeypatch.setattr(worktree.settings, "GITHUB_TOKEN", "")
    worktree._repo_locks.clear()
    repo_url = "https://example.com/org/repo.git"
    repo_path = worktree.get_managed_repo_path("proj-3", repo_url)
    (repo_path / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree, "_run_with_retry", AsyncMock(return_value=(0, "", "")))

    async def _fake_run(cmd: str, cwd=None):
        if cmd == "git remote get-url origin":
            return 0, repo_url, ""
        if cmd.startswith("git rev-parse --verify --quiet"):
            return 1, "", "not found"
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _fake_run)

    resolved = await worktree.ensure_repo_local_mirror("proj-3", repo_url, base_branch="release")
    assert resolved is None


@pytest.mark.asyncio
async def test_prepare_workspace_from_repo_checks_out_target_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "")
    monkeypatch.setattr(worktree.settings, "GITHUB_TOKEN", "")
    workspace = tmp_path / "workspace"
    retry_calls: list[str] = []
    run_calls: list[str] = []

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        retry_calls.append(cmd)
        if cmd.startswith("git clone --origin origin"):
            (workspace / ".git").mkdir(parents=True, exist_ok=True)
        return 0, "", ""

    async def _fake_run(cmd: str, cwd=None):
        run_calls.append(cmd)
        if "origin/task/existing-branch" in cmd:
            return 0, "", ""
        if cmd.startswith("git checkout -B"):
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)
    monkeypatch.setattr(worktree, "_run", _fake_run)

    ok, branch, error = await worktree.prepare_workspace_from_repo(
        workspace=str(workspace),
        repo_url="https://example.com/org/repo.git",
        task_id="task-prepare-1",
        task_title="prepare branch",
        base_branch="main",
        target_branch="task/existing-branch",
    )
    assert ok is True
    assert branch == "task/existing-branch"
    assert error is None
    assert any(c.startswith("git clone --origin origin") for c in retry_calls)
    assert any(c.startswith("git checkout -B") for c in run_calls)


@pytest.mark.asyncio
async def test_prepare_workspace_from_repo_creates_branch_from_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "workspace-2"
    run_calls: list[str] = []

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        if cmd.startswith("git clone --origin origin"):
            (workspace / ".git").mkdir(parents=True, exist_ok=True)
        return 0, "", ""

    async def _fake_run(cmd: str, cwd=None):
        run_calls.append(cmd)
        if "origin/task/new-branch" in cmd:
            return 1, "", "not found"
        if "origin/release" in cmd:
            return 0, "", ""
        if cmd.startswith("git checkout -b"):
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)
    monkeypatch.setattr(worktree, "_run", _fake_run)

    ok, branch, error = await worktree.prepare_workspace_from_repo(
        workspace=str(workspace),
        repo_url="https://example.com/org/repo.git",
        task_id="task-prepare-2",
        task_title="prepare branch new",
        base_branch="release",
        target_branch="task/new-branch",
    )
    assert ok is True
    assert branch == "task/new-branch"
    assert error is None
    assert any(c.startswith("git checkout -b") for c in run_calls)


@pytest.mark.asyncio
async def test_commit_and_push_workspace_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace-push"
    workspace.mkdir(parents=True)

    async def _run_no_change(cmd: str, cwd=None):
        if cmd == "git status --porcelain":
            return 0, "", ""
        if cmd == "git branch --show-current":
            return 0, "feat/no-change", ""
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _run_no_change)
    assert await worktree.commit_and_push_workspace(
        workspace=str(workspace),
        commit_message="msg",
    ) == "feat/no-change"

    retry_calls: list[str] = []

    async def _run_change(cmd: str, cwd=None):
        if cmd == "git status --porcelain":
            return 0, " M a.py", ""
        if cmd == "git branch --show-current":
            return 0, "feat/current", ""
        if cmd == "git remote get-url origin":
            return 0, "https://example.com/org/repo.git", ""
        return 0, "", ""

    async def _retry(cmd: str, cwd=None, **kwargs):
        retry_calls.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _run_change)
    monkeypatch.setattr(worktree, "_run_with_retry", _retry)
    monkeypatch.setattr(worktree, "_inject_git_auth", lambda cmd, _repo_url: f"AUTH::{cmd}")

    branch = await worktree.commit_and_push_workspace(
        workspace=str(workspace),
        commit_message="feat: hello",
        target_branch="task/override",
    )
    assert branch == "task/override"
    assert retry_calls
    assert retry_calls[0] == "AUTH::git push -u origin HEAD:task/override"


@pytest.mark.asyncio
async def test_create_pr_for_workspace_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace-pr"
    workspace.mkdir(parents=True)
    captured: list[str] = []

    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "https://scm.example.com/api/v3")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "secret-token")

    async def _retry_success(cmd: str, cwd=None, **kwargs):
        captured.append(cmd)
        return 0, "https://scm.example.com/org/repo/pull/2", ""

    monkeypatch.setattr(worktree, "_run_with_retry", _retry_success)

    pr_url = await worktree.create_pr_for_workspace(
        workspace=str(workspace),
        title="task title",
        body="task body",
        base_branch="main",
    )
    assert pr_url == "https://scm.example.com/org/repo/pull/2"
    assert captured
    assert "GH_HOST=scm.example.com" in captured[0]

    monkeypatch.setattr(worktree, "_run_with_retry", AsyncMock(return_value=(1, "", "failed")))
    failed_url = await worktree.create_pr_for_workspace(
        workspace=str(workspace),
        title="task title",
        body="task body",
    )
    assert failed_url is None


@pytest.mark.asyncio
async def test_create_worktree_repo_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(tmp_path / "wt"))
    manager = worktree.WorktreeManager(str(tmp_path / "missing-repo"))

    result = await manager.create_worktree("task-a", "title")
    assert result is None


@pytest.mark.asyncio
async def test_create_worktree_returns_existing_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wt_base = tmp_path / "wt"

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(wt_base))
    manager = worktree.WorktreeManager(str(repo))

    existing = wt_base / "task-1"
    existing.mkdir(parents=True)

    result = await manager.create_worktree("task-1", "title")
    assert result == str(existing)


@pytest.mark.asyncio
async def test_create_worktree_success_and_fallback_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(tmp_path / "wt"))
    manager = worktree.WorktreeManager(str(repo))

    run_calls: list[str] = []

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        return 0, "", ""

    async def _fake_run(cmd: str, cwd=None):
        run_calls.append(cmd)
        if "-b" in cmd:
            return 1, "", "branch exists"
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)
    monkeypatch.setattr(worktree, "_run", _fake_run)

    result = await manager.create_worktree("task-2", "My task", base_branch="master")
    assert result is not None
    assert any("git worktree add -b" in c for c in run_calls)
    assert any("git worktree add" in c and "-b" not in c for c in run_calls)


@pytest.mark.asyncio
async def test_create_worktree_failure_after_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(tmp_path / "wt"))
    manager = worktree.WorktreeManager(str(repo))

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        return 0, "", ""

    async def _fake_run(cmd: str, cwd=None):
        return 1, "", "still failing"

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)
    monkeypatch.setattr(worktree, "_run", _fake_run)

    result = await manager.create_worktree("task-3", "title")
    assert result is None


@pytest.mark.asyncio
async def test_cleanup_worktree_when_missing_only_prunes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(tmp_path / "wt"))
    manager = worktree.WorktreeManager(str(repo))

    calls: list[str] = []

    async def _fake_run(cmd: str, cwd=None):
        calls.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _fake_run)

    await manager.cleanup_worktree("no-such-task")
    assert calls == ["git worktree prune"]


@pytest.mark.asyncio
async def test_cleanup_worktree_remove_failure_falls_back_to_rmtree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    wt_base = tmp_path / "wt"
    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(wt_base))
    manager = worktree.WorktreeManager(str(repo))

    task_dir = wt_base / "task-4"
    task_dir.mkdir(parents=True)
    (task_dir / "x.txt").write_text("x", encoding="utf-8")

    run_calls: list[str] = []

    async def _fake_run(cmd: str, cwd=None):
        run_calls.append(cmd)
        if cmd.startswith("git worktree remove"):
            return 1, "", "remove failed"
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _fake_run)

    await manager.cleanup_worktree("task-4")
    assert not task_dir.exists()
    assert any(cmd == "git worktree prune" for cmd in run_calls)


@pytest.mark.asyncio
async def test_prune_all_stale_removes_orphans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wt_base = tmp_path / "wt"

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(wt_base))
    manager = worktree.WorktreeManager(str(repo))

    valid_dir = wt_base / "task-valid"
    stale_dir = wt_base / "task-stale"
    valid_dir.mkdir(parents=True)
    stale_dir.mkdir(parents=True)

    async def _fake_run(cmd: str, cwd=None):
        if cmd == "git worktree list --porcelain":
            return 0, f"worktree {valid_dir}\n", ""
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _fake_run)

    cleaned = await manager.prune_all_stale()
    assert cleaned == 1
    assert valid_dir.exists()
    assert not stale_dir.exists()


@pytest.mark.asyncio
async def test_commit_and_push_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wt_base = tmp_path / "wt"

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(wt_base))
    manager = worktree.WorktreeManager(str(repo))

    # Missing worktree
    assert await manager.commit_and_push("missing", "msg") is None

    task_dir = wt_base / "task-5"
    task_dir.mkdir(parents=True)

    # No changes -> returns current branch
    async def _run_no_changes(cmd: str, cwd=None):
        if cmd == "git status --porcelain":
            return 0, "", ""
        if cmd == "git branch --show-current":
            return 0, "feat/no-change", ""
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _run_no_changes)
    assert await manager.commit_and_push("task-5", "msg") == "feat/no-change"

    # With changes and full success
    run_calls: list[str] = []

    async def _run_success(cmd: str, cwd=None):
        run_calls.append(cmd)
        if cmd == "git status --porcelain":
            return 0, " M a.py", ""
        if cmd == "git branch --show-current":
            return 0, "feat/with-change", ""
        return 0, "", ""

    async def _retry_success(cmd: str, cwd=None, **kwargs):
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _run_success)
    monkeypatch.setattr(worktree, "_run_with_retry", _retry_success)

    assert await manager.commit_and_push("task-5", "commit message") == "feat/with-change"
    assert "git add -A" in run_calls


@pytest.mark.asyncio
async def test_commit_and_push_failure_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wt_base = tmp_path / "wt"

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(wt_base))
    manager = worktree.WorktreeManager(str(repo))

    task_dir = wt_base / "task-6"
    task_dir.mkdir(parents=True)

    async def _run_add_fail(cmd: str, cwd=None):
        if cmd == "git status --porcelain":
            return 0, " M x.py", ""
        if cmd == "git add -A":
            return 1, "", "add failed"
        return 0, "", ""

    monkeypatch.setattr(worktree, "_run", _run_add_fail)
    assert await manager.commit_and_push("task-6", "msg") is None


@pytest.mark.asyncio
async def test_create_pr_and_manager_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    wt_base = tmp_path / "wt"

    monkeypatch.setattr(worktree.settings, "WORKTREE_BASE_DIR", str(wt_base))
    monkeypatch.setattr(worktree.settings, "GHE_BASE_URL", "https://scm.example.com")
    monkeypatch.setattr(worktree.settings, "GHE_TOKEN", "secret")

    manager = worktree.WorktreeManager(str(repo))
    task_dir = wt_base / "task-7"
    task_dir.mkdir(parents=True)

    captured_cmd: list[str] = []

    async def _fake_retry(cmd: str, cwd=None, **kwargs):
        captured_cmd.append(cmd)
        return 0, "https://scm.example.com/org/repo/pull/1", ""

    monkeypatch.setattr(worktree, "_run_with_retry", _fake_retry)

    pr_url = await manager.create_pr("task-7", "title", "body", base_branch="master")
    assert pr_url == "https://scm.example.com/org/repo/pull/1"
    assert captured_cmd
    assert "GH_HOST=scm.example.com" in captured_cmd[0]

    worktree._managers.clear()
    m1 = worktree.get_worktree_manager(str(repo))
    m2 = worktree.get_worktree_manager(str(repo))
    assert m1 is m2
