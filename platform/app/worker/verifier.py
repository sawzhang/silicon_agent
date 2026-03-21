"""Objective stage verifier: run shell commands to validate stage output.

Replaces LLM self-assessment with deterministic verification via external tools
(pytest, ruff, tsc, etc.). Inspired by autoresearch's keep/discard loop where
an objective metric (val_bpb) drives iteration decisions.

Usage in template stage definition:
    "evaluator": {
        "enabled": true,
        "type": "objective",
        "commands": ["ruff check .", "pytest tests/ --tb=short -q"],
        "success_criteria": "all_pass",   # or "any_pass"
        "max_iterations": 5,
        "token_budget": 50000
    }
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result of a single verification command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class VerifyResult:
    """Aggregated result of all verification commands."""

    passed: bool
    command_results: List[CommandResult] = field(default_factory=list)
    summary: str = ""

    @property
    def failure_details(self) -> str:
        """Human-readable failure details for LLM consumption."""
        parts: list[str] = []
        for cr in self.command_results:
            if cr.passed:
                continue
            if cr.timed_out:
                parts.append(f"[TIMEOUT] `{cr.command}` 超时")
                continue
            # Combine stderr and stdout, prefer stderr for error info
            output = (cr.stderr.strip() or cr.stdout.strip())[-2000:]
            parts.append(
                f"[FAIL] `{cr.command}` (exit_code={cr.exit_code})\n{output}"
            )
        return "\n\n".join(parts) if parts else ""

    @property
    def metrics(self) -> dict:
        """Structured metrics for logging."""
        total = len(self.command_results)
        passed = sum(1 for cr in self.command_results if cr.passed)
        return {
            "total_commands": total,
            "passed_commands": passed,
            "pass_rate": passed / total if total > 0 else 0.0,
        }


async def run_verify_command(
    command: str,
    cwd: str,
    timeout: Optional[float] = None,
) -> CommandResult:
    """Run a single verification command in a subprocess.

    Args:
        command: Shell command string to execute.
        cwd: Working directory for the command.
        timeout: Max seconds to wait. Defaults to settings.VERIFIER_CMD_TIMEOUT.

    Returns:
        CommandResult with exit code, stdout, stderr, timeout flag.
    """
    if timeout is None:
        timeout = settings.VERIFIER_CMD_TIMEOUT

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Limit environment leakage — inherit parent env
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                timed_out=True,
            )

        # Cap output to prevent memory bloat
        max_output = 8000
        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")[-max_output:]
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")[-max_output:]

        return CommandResult(
            command=command,
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )
    except Exception as e:
        logger.warning("Verify command failed to execute: %s", e, exc_info=True)
        return CommandResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr=f"Failed to execute: {e}",
        )


async def run_verification(
    commands: List[str],
    cwd: str,
    success_criteria: str = "all_pass",
    timeout: Optional[float] = None,
) -> VerifyResult:
    """Run a list of verification commands and aggregate results.

    Args:
        commands: List of shell commands to run sequentially.
        cwd: Working directory.
        success_criteria: "all_pass" (default) or "any_pass".
        timeout: Per-command timeout override.

    Returns:
        VerifyResult with aggregated pass/fail and details.
    """
    if not commands:
        return VerifyResult(passed=True, summary="No verification commands configured")

    results: List[CommandResult] = []
    for cmd in commands:
        result = await run_verify_command(cmd, cwd=cwd, timeout=timeout)
        results.append(result)
        logger.info(
            "Verify [%s] exit_code=%d passed=%s",
            cmd, result.exit_code, result.passed,
        )

    if success_criteria == "any_pass":
        passed = any(r.passed for r in results)
    else:  # all_pass
        passed = all(r.passed for r in results)

    passed_count = sum(1 for r in results if r.passed)
    total = len(results)
    summary = f"验证结果: {passed_count}/{total} 通过"
    if not passed:
        failed_cmds = [r.command for r in results if not r.passed]
        summary += f" — 失败: {', '.join(failed_cmds)}"

    return VerifyResult(passed=passed, command_results=results, summary=summary)


def build_fix_prompt(
    stage_name: str,
    iteration: int,
    max_iterations: int,
    verify_result: VerifyResult,
) -> str:
    """Build a targeted fix prompt from verification failures.

    Unlike the LLM self-assessment approach, this gives the agent concrete,
    actionable feedback from real tool output.
    """
    details = verify_result.failure_details
    metrics = verify_result.metrics

    return (
        f"## 第 {iteration}/{max_iterations} 轮客观验证未通过\n\n"
        f"通过率: {metrics['passed_commands']}/{metrics['total_commands']}\n\n"
        f"### 失败详情\n\n{details}\n\n"
        f"### 要求\n\n"
        f"1. 仔细分析上述错误输出，定位根因\n"
        f"2. 只修复导致验证失败的问题，不要重写整体代码\n"
        f"3. 修复后确保所有验证命令能通过"
    )
