"""Git worktree lifecycle management for isolated agent execution.

Each task gets its own git worktree (isolated branch + directory), enabling
coding/test agents to make real git operations without interfering with
each other or the main working copy.

Inspired by OpenClaw's agent-per-worktree pattern and Stripe Minions' devbox isolation.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import shlex
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger(__name__)


async def _run(cmd: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
    """Run a shell command asynchronously. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def _run_with_retry(
    cmd: str,
    cwd: Optional[str] = None,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> tuple[int, str, str]:
    """Run a shell command with exponential backoff retry for transient failures."""
    last_rc, last_out, last_err = 0, "", ""
    for attempt in range(max_retries):
        last_rc, last_out, last_err = await _run(cmd, cwd=cwd)
        if last_rc == 0:
            return last_rc, last_out, last_err
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Git command failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, max_retries, delay, last_err[:200],
            )
            await asyncio.sleep(delay)
    return last_rc, last_out, last_err


def _sanitize_branch_name(task_id: str, task_title: str) -> str:
    """Generate a valid git branch name from task info."""
    # Use first 8 chars of task_id + sanitized title
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", task_title)[:40].strip("-")
    return f"task/{task_id[:8]}-{slug}"


def get_managed_repo_path(project_id: str, repo_url: str) -> Path:
    """Resolve deterministic local cache path for a remote repo."""
    configured_cache_dir = (settings.WORKTREE_REPO_CACHE_DIR or "").strip()
    base_dir = (
        Path(configured_cache_dir)
        if configured_cache_dir
        else Path(settings.WORKTREE_BASE_DIR) / "repos"
    )
    safe_project_id = (project_id or "").strip()
    if safe_project_id:
        key = re.sub(r"[^a-zA-Z0-9._-]+", "-", safe_project_id).strip("-")
    else:
        digest = hashlib.sha1(repo_url.encode("utf-8")).hexdigest()[:16]
        key = f"repo-{digest}"
    return base_dir / key


def _resolve_git_token_for_repo_url(repo_url: str) -> str:
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return ""

    github_token = (settings.GITHUB_TOKEN or "").strip()
    ghe_token = (settings.GHE_TOKEN or "").strip()
    if "github.com" in repo_url:
        return github_token

    if settings.GHE_BASE_URL and ghe_token:
        ghe_host = urlparse(settings.GHE_BASE_URL).hostname or ""
        if ghe_host and ghe_host in repo_url:
            return ghe_token
    if ghe_token and "github.com" not in repo_url:
        return ghe_token
    return ""


def _inject_git_auth(cmd: str, repo_url: str) -> str:
    repo_url = (repo_url or "").strip()
    token = _resolve_git_token_for_repo_url(repo_url)
    if not token:
        return cmd
    if not repo_url.startswith(("http://", "https://")):
        return cmd
    if not cmd.startswith("git "):
        return cmd

    raw = f"x-access-token:{token}".encode("utf-8")
    auth_value = "Basic " + base64.b64encode(raw).decode("ascii")
    header = f"Authorization: {auth_value}"
    return f"git -c http.extraheader={shlex.quote(header)} {cmd[4:]}"


async def ensure_repo_local_mirror(
    project_id: str,
    repo_url: str,
    base_branch: str = "main",
) -> Optional[str]:
    """Ensure a local git mirror exists and is up-to-date for worktree creation.

    Returns the local repo path on success, None on failure.
    """
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return None

    branch = (base_branch or "").strip() or "main"
    repo_path = get_managed_repo_path(project_id, repo_url)
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    lock_key = str(repo_path)
    lock = _repo_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        git_dir = repo_path / ".git"
        if git_dir.exists():
            rc, origin_url, _ = await _run("git remote get-url origin", cwd=str(repo_path))
            if rc != 0 or origin_url.strip() != repo_url:
                logger.warning(
                    "Managed repo remote mismatch, recloning: path=%s expected=%s actual=%s",
                    repo_path,
                    repo_url,
                    origin_url.strip() if rc == 0 else "<unavailable>",
                )
                shutil.rmtree(repo_path, ignore_errors=True)

        if not (repo_path / ".git").exists():
            clone_cmd = (
                f"git clone --origin origin {shlex.quote(repo_url)} {shlex.quote(str(repo_path))}"
            )
            rc, _, err = await _run_with_retry(_inject_git_auth(clone_cmd, repo_url))
            if rc != 0:
                logger.error("git clone failed for project %s: %s", project_id, err)
                return None

        fetch_cmd = _inject_git_auth("git fetch --prune origin", repo_url)
        rc, _, err = await _run_with_retry(fetch_cmd, cwd=str(repo_path))
        if rc != 0:
            logger.error("git fetch failed for managed repo %s: %s", repo_path, err)
            return None

        remote_ref = f"origin/{branch}"
        rc, _, _ = await _run(
            f"git rev-parse --verify --quiet {shlex.quote(remote_ref)}",
            cwd=str(repo_path),
        )
        if rc != 0:
            logger.error(
                "Configured branch not found on remote for project %s: %s",
                project_id,
                remote_ref,
            )
            return None

        checkout_cmd = (
            f"git checkout -B {shlex.quote(branch)} {shlex.quote(remote_ref)}"
        )
        rc, _, err = await _run(checkout_cmd, cwd=str(repo_path))
        if rc != 0:
            logger.error(
                "git checkout failed for managed repo %s (branch=%s): %s",
                repo_path,
                branch,
                err,
            )
            return None

    return str(repo_path)


async def prepare_workspace_from_repo(
    *,
    workspace: str,
    repo_url: str,
    task_id: str,
    task_title: str,
    base_branch: str = "main",
    target_branch: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Prepare a task workspace by cloning remote repo and checking out task branch.

    Returns (success, branch_name, error_message).
    """
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return False, None, "repo_url is required"

    branch = (base_branch or "").strip() or "main"
    branch_name = (target_branch or "").strip() or _sanitize_branch_name(task_id, task_title)
    workspace_path = Path(workspace)

    if workspace_path.exists():
        shutil.rmtree(workspace_path, ignore_errors=True)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)

    clone_cmd = (
        f"git clone --origin origin {shlex.quote(repo_url)} {shlex.quote(str(workspace_path))}"
    )
    rc, _, err = await _run_with_retry(_inject_git_auth(clone_cmd, repo_url))
    if rc != 0:
        return False, None, err or "git clone failed"

    cwd = str(workspace_path)
    remote_target_ref = f"origin/{branch_name}"
    rc, _, _ = await _run(
        f"git rev-parse --verify --quiet {shlex.quote(remote_target_ref)}",
        cwd=cwd,
    )
    if rc == 0:
        checkout_cmd = (
            f"git checkout -B {shlex.quote(branch_name)} {shlex.quote(remote_target_ref)}"
        )
        rc, _, err = await _run(checkout_cmd, cwd=cwd)
        if rc != 0:
            return False, None, err or "git checkout target branch failed"
        return True, branch_name, None

    remote_base_ref = f"origin/{branch}"
    rc, _, _ = await _run(
        f"git rev-parse --verify --quiet {shlex.quote(remote_base_ref)}",
        cwd=cwd,
    )
    if rc != 0:
        return False, None, f"base branch not found on remote: {remote_base_ref}"

    checkout_cmd = (
        f"git checkout -b {shlex.quote(branch_name)} {shlex.quote(remote_base_ref)}"
    )
    rc, _, err = await _run(checkout_cmd, cwd=cwd)
    if rc != 0:
        return False, None, err or "git checkout base branch failed"

    return True, branch_name, None


async def commit_and_push_workspace(
    *,
    workspace: str,
    commit_message: str,
    target_branch: Optional[str] = None,
) -> Optional[str]:
    """Commit and push changes from an arbitrary workspace git checkout."""
    workspace_path = Path(workspace)
    if not workspace_path.exists():
        return None

    cwd = str(workspace_path)
    rc, out, _ = await _run("git status --porcelain", cwd=cwd)
    if rc != 0:
        return None
    if not out.strip():
        rc, branch, _ = await _run("git branch --show-current", cwd=cwd)
        return branch if rc == 0 else None

    rc, _, err = await _run("git add -A", cwd=cwd)
    if rc != 0:
        logger.error("git add failed in workspace %s: %s", workspace_path, err)
        return None

    rc, _, err = await _run(f"git commit -m {shlex.quote(commit_message)}", cwd=cwd)
    if rc != 0:
        logger.error("git commit failed in workspace %s: %s", workspace_path, err)
        return None

    rc, branch, _ = await _run("git branch --show-current", cwd=cwd)
    if rc != 0:
        return None

    push_branch = (target_branch or "").strip() or branch
    if push_branch == branch:
        push_cmd = f"git push -u origin {shlex.quote(branch)}"
    else:
        push_cmd = f"git push -u origin HEAD:{shlex.quote(push_branch)}"

    origin_rc, origin_url, _ = await _run("git remote get-url origin", cwd=cwd)
    normalized_origin = origin_url.strip() if origin_rc == 0 else ""
    push_cmd = _inject_git_auth(push_cmd, normalized_origin)
    rc, _, err = await _run_with_retry(push_cmd, cwd=cwd)
    if rc != 0:
        logger.error("git push failed in workspace %s: %s", workspace_path, err)
        return None

    return push_branch


async def create_pr_for_workspace(
    *,
    workspace: str,
    title: str,
    body: str,
    base_branch: str = "main",
) -> Optional[str]:
    """Create PR from an arbitrary workspace git checkout via gh CLI."""
    workspace_path = Path(workspace)
    if not workspace_path.exists():
        return None

    cwd = str(workspace_path)
    cmd = (
        f"gh pr create --title {shlex.quote(title)} "
        f"--body {shlex.quote(body)} "
        f"--base {shlex.quote(base_branch)}"
    )

    if settings.GHE_BASE_URL:
        ghe_host = urlparse(settings.GHE_BASE_URL).hostname or ""
        if ghe_host:
            cmd = (
                f'GH_HOST={ghe_host} '
                f'GH_ENTERPRISE_TOKEN={settings.GHE_TOKEN} '
                f'{cmd}'
            )

    rc, out, err = await _run_with_retry(cmd, cwd=cwd)
    if rc != 0:
        logger.error("gh pr create failed in workspace %s: %s", workspace_path, err)
        return None
    return out.strip() or None


class WorktreeManager:
    """Manages git worktree creation and cleanup for task execution."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.base_dir = Path(settings.WORKTREE_BASE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def create_worktree(
        self,
        task_id: str,
        task_title: str = "",
        base_branch: str = "main",
        target_branch: Optional[str] = None,
    ) -> Optional[str]:
        """Create an isolated git worktree for a task.

        Returns the worktree path, or None on failure.
        """
        if not self.repo_path.exists():
            logger.error("Repo path does not exist: %s", self.repo_path)
            return None

        branch_name = (target_branch or "").strip() or _sanitize_branch_name(task_id, task_title)
        worktree_path = self.base_dir / task_id

        if worktree_path.exists():
            logger.info("Worktree already exists for task %s: %s", task_id, worktree_path)
            return str(worktree_path)

        origin_rc, origin_url, _ = await _run("git remote get-url origin", cwd=str(self.repo_path))
        normalized_origin = origin_url.strip() if origin_rc == 0 else ""

        # Fetch latest from remote (best-effort, with retry)
        fetch_cmd = _inject_git_auth("git fetch origin", normalized_origin)
        await _run_with_retry(fetch_cmd, cwd=str(self.repo_path))

        # If target branch exists remotely, work on that branch directly.
        if target_branch:
            rc, _, _ = await _run(
                f"git rev-parse --verify --quiet origin/{branch_name}",
                cwd=str(self.repo_path),
            )
            if rc == 0:
                rc, out, err = await _run(
                    f"git worktree add -B {branch_name} {worktree_path} origin/{branch_name}",
                    cwd=str(self.repo_path),
                )
            else:
                rc, out, err = await _run(
                    f"git worktree add -b {branch_name} {worktree_path} origin/{base_branch}",
                    cwd=str(self.repo_path),
                )
        else:
            # Create worktree with new branch from base
            rc, out, err = await _run(
                f"git worktree add -b {branch_name} {worktree_path} origin/{base_branch}",
                cwd=str(self.repo_path),
            )

        if rc != 0:
            # Branch might already exist, try without -b
            rc, out, err = await _run(
                f"git worktree add {worktree_path} {branch_name}",
                cwd=str(self.repo_path),
            )

        if rc != 0:
            logger.error(
                "Failed to create worktree for task %s: %s", task_id, err,
            )
            return None

        logger.info(
            "Created worktree for task %s: branch=%s path=%s",
            task_id, branch_name, worktree_path,
        )
        return str(worktree_path)

    async def cleanup_worktree(self, task_id: str) -> None:
        """Remove a task's worktree and its stale git refs."""
        worktree_path = self.base_dir / task_id

        if not worktree_path.exists():
            # Directory gone but git refs might still exist — prune anyway
            await _run("git worktree prune", cwd=str(self.repo_path))
            return

        # Remove worktree via git
        rc, _, err = await _run(
            f"git worktree remove {worktree_path} --force",
            cwd=str(self.repo_path),
        )
        if rc != 0:
            logger.warning("git worktree remove failed: %s, falling back to rm", err)
            shutil.rmtree(worktree_path, ignore_errors=True)

        # Prune stale worktree refs
        await _run("git worktree prune", cwd=str(self.repo_path))

        logger.info("Cleaned up worktree for task %s", task_id)

    async def prune_all_stale(self) -> int:
        """Remove orphaned worktree directories and prune stale git refs.

        Scans WORKTREE_BASE_DIR for leftover directories and removes them.
        Returns the number of orphans cleaned up.
        """
        if not self.base_dir.exists():
            return 0

        # Get list of valid worktrees from git
        rc, out, _ = await _run("git worktree list --porcelain", cwd=str(self.repo_path))
        valid_paths: set[str] = set()
        if rc == 0:
            for line in out.splitlines():
                if line.startswith("worktree "):
                    valid_paths.add(line[len("worktree "):].strip())

        cleaned = 0
        for entry in self.base_dir.iterdir():
            if not entry.is_dir():
                continue
            if str(entry) in valid_paths:
                continue
            # Orphaned directory — not tracked by git worktree list
            logger.info("Removing orphaned worktree directory: %s", entry)
            shutil.rmtree(entry, ignore_errors=True)
            cleaned += 1

        # Prune any stale git refs
        await _run("git worktree prune", cwd=str(self.repo_path))

        if cleaned:
            logger.info("Pruned %d orphaned worktree(s)", cleaned)
        return cleaned

    async def commit_and_push(
        self, task_id: str, commit_message: str, target_branch: Optional[str] = None,
    ) -> Optional[str]:
        """Stage all changes, commit, and push in the task's worktree.

        Returns the branch name on success, None on failure.
        """
        worktree_path = self.base_dir / task_id
        if not worktree_path.exists():
            return None

        cwd = str(worktree_path)

        # Check if there are changes
        rc, out, _ = await _run("git status --porcelain", cwd=cwd)
        rc, branch, _ = await _run("git branch --show-current", cwd=cwd)
        if rc != 0 or not branch:
            return None
        push_branch = (target_branch or "").strip() or branch
        if push_branch == branch:
            push_cmd = f"git push -u origin {shlex.quote(branch)}"
        else:
            push_cmd = f"git push -u origin HEAD:{shlex.quote(push_branch)}"
        origin_rc, origin_url, _ = await _run("git remote get-url origin", cwd=cwd)
        normalized_origin = origin_url.strip() if origin_rc == 0 else ""
        push_cmd = _inject_git_auth(push_cmd, normalized_origin)

        if not out.strip():
            logger.info("No changes to commit for task %s", task_id)
            # Even without new commits, ensure target branch exists remotely.
            rc, _, err = await _run_with_retry(push_cmd, cwd=cwd)
            if rc != 0:
                logger.error("git push failed for task %s after retries: %s", task_id, err)
                return None
            return push_branch

        # Stage all changes
        rc, _, err = await _run("git add -A", cwd=cwd)
        if rc != 0:
            logger.error("git add failed for task %s: %s", task_id, err)
            return None

        # Commit
        rc, _, err = await _run(
            f"git commit -m {shlex.quote(commit_message)}",
            cwd=cwd,
        )
        if rc != 0:
            logger.error("git commit failed for task %s: %s", task_id, err)
            return None

        # Push to remote branch. If target_branch is set, push HEAD to it.

        rc, _, err = await _run_with_retry(push_cmd, cwd=cwd)
        if rc != 0:
            logger.error("git push failed for task %s after retries: %s", task_id, err)
            return None

        logger.info("Committed and pushed for task %s on branch %s", task_id, push_branch)
        return push_branch

    async def create_pr(
        self,
        task_id: str,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> Optional[str]:
        """Create a PR via gh CLI. Supports both github.com and GitHub Enterprise."""
        worktree_path = self.base_dir / task_id
        if not worktree_path.exists():
            return None

        cwd = str(worktree_path)
        cmd = (
            f"gh pr create --title {shlex.quote(title)} "
            f"--body {shlex.quote(body)} "
            f"--base {shlex.quote(base_branch)}"
        )

        # For GitHub Enterprise, set GH_HOST so `gh` CLI targets the right server
        if settings.GHE_BASE_URL:
            from urllib.parse import urlparse
            ghe_host = urlparse(settings.GHE_BASE_URL).hostname or ""
            if ghe_host:
                cmd = (
                    f'GH_HOST={ghe_host} '
                    f'GH_ENTERPRISE_TOKEN={settings.GHE_TOKEN} '
                    f'{cmd}'
                )

        rc, out, err = await _run_with_retry(cmd, cwd=cwd)
        if rc != 0:
            logger.error("gh pr create failed for task %s after retries: %s", task_id, err)
            return None

        pr_url = out.strip()
        logger.info("Created PR for task %s: %s", task_id, pr_url)
        return pr_url


# Module-level cache of WorktreeManager instances per repo path
_managers: dict[str, WorktreeManager] = {}
_repo_locks: dict[str, asyncio.Lock] = {}


def get_worktree_manager(repo_path: str) -> WorktreeManager:
    """Get or create a WorktreeManager for a given repo path."""
    if repo_path not in _managers:
        _managers[repo_path] = WorktreeManager(repo_path)
    return _managers[repo_path]
