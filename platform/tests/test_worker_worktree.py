from __future__ import annotations

from pathlib import Path

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
