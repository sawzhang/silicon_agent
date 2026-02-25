"""Git worktree lifecycle management for isolated agent execution.

Each task gets its own git worktree (isolated branch + directory), enabling
coding/test agents to make real git operations without interfering with
each other or the main working copy.

Inspired by OpenClaw's agent-per-worktree pattern and Stripe Minions' devbox isolation.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

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


def _sanitize_branch_name(task_id: str, task_title: str) -> str:
    """Generate a valid git branch name from task info."""
    # Use first 8 chars of task_id + sanitized title
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", task_title)[:40].strip("-")
    return f"task/{task_id[:8]}-{slug}"


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
    ) -> Optional[str]:
        """Create an isolated git worktree for a task.

        Returns the worktree path, or None on failure.
        """
        if not self.repo_path.exists():
            logger.error("Repo path does not exist: %s", self.repo_path)
            return None

        branch_name = _sanitize_branch_name(task_id, task_title)
        worktree_path = self.base_dir / task_id

        if worktree_path.exists():
            logger.info("Worktree already exists for task %s: %s", task_id, worktree_path)
            return str(worktree_path)

        # Fetch latest from remote (best-effort)
        await _run("git fetch origin", cwd=str(self.repo_path))

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
        """Remove a task's worktree and optionally its branch."""
        worktree_path = self.base_dir / task_id

        if not worktree_path.exists():
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

    async def commit_and_push(
        self, task_id: str, commit_message: str,
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
        if not out.strip():
            logger.info("No changes to commit for task %s", task_id)
            # Still return branch name for PR creation
            rc, branch, _ = await _run("git branch --show-current", cwd=cwd)
            return branch if rc == 0 else None

        # Stage all changes
        rc, _, err = await _run("git add -A", cwd=cwd)
        if rc != 0:
            logger.error("git add failed for task %s: %s", task_id, err)
            return None

        # Commit
        rc, _, err = await _run(
            f'git commit -m "{commit_message}"',
            cwd=cwd,
        )
        if rc != 0:
            logger.error("git commit failed for task %s: %s", task_id, err)
            return None

        # Push to remote
        rc, branch, _ = await _run("git branch --show-current", cwd=cwd)
        if rc != 0:
            return None

        rc, _, err = await _run(
            f"git push -u origin {branch}",
            cwd=cwd,
        )
        if rc != 0:
            logger.error("git push failed for task %s: %s", task_id, err)
            return None

        logger.info("Committed and pushed for task %s on branch %s", task_id, branch)
        return branch

    async def create_pr(
        self,
        task_id: str,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> Optional[str]:
        """Create a GitHub PR via gh CLI. Returns PR URL or None."""
        worktree_path = self.base_dir / task_id
        if not worktree_path.exists():
            return None

        cwd = str(worktree_path)
        rc, out, err = await _run(
            f'gh pr create --title "{title}" --body "{body}" --base {base_branch}',
            cwd=cwd,
        )
        if rc != 0:
            logger.error("gh pr create failed for task %s: %s", task_id, err)
            return None

        pr_url = out.strip()
        logger.info("Created PR for task %s: %s", task_id, pr_url)
        return pr_url


# Module-level cache of WorktreeManager instances per repo path
_managers: dict[str, WorktreeManager] = {}


def get_worktree_manager(repo_path: str) -> WorktreeManager:
    """Get or create a WorktreeManager for a given repo path."""
    if repo_path not in _managers:
        _managers[repo_path] = WorktreeManager(repo_path)
    return _managers[repo_path]
