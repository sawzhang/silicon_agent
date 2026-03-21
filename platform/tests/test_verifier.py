"""Unit tests for worker/verifier.py — objective stage verification."""
import tempfile

import pytest

from app.worker.verifier import (
    CommandResult,
    VerifyResult,
    build_fix_prompt,
    run_verification,
    run_verify_command,
)


@pytest.fixture
def tmp_workdir():
    """Create a temporary working directory for verification commands."""
    d = tempfile.mkdtemp(prefix="test_verifier_")
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# CommandResult unit tests
# ---------------------------------------------------------------------------


class TestCommandResult:
    def test_passed_when_exit_zero(self):
        cr = CommandResult(command="echo ok", exit_code=0, stdout="ok", stderr="")
        assert cr.passed is True

    def test_failed_when_exit_nonzero(self):
        cr = CommandResult(command="false", exit_code=1, stdout="", stderr="error")
        assert cr.passed is False

    def test_failed_when_timed_out(self):
        cr = CommandResult(
            command="sleep 999", exit_code=0, stdout="", stderr="", timed_out=True,
        )
        assert cr.passed is False


# ---------------------------------------------------------------------------
# VerifyResult unit tests
# ---------------------------------------------------------------------------


class TestVerifyResult:
    def test_all_pass(self):
        results = [
            CommandResult(command="a", exit_code=0, stdout="", stderr=""),
            CommandResult(command="b", exit_code=0, stdout="", stderr=""),
        ]
        vr = VerifyResult(passed=True, command_results=results)
        assert vr.failure_details == ""
        assert vr.metrics["pass_rate"] == 1.0

    def test_partial_fail(self):
        results = [
            CommandResult(command="a", exit_code=0, stdout="", stderr=""),
            CommandResult(command="b", exit_code=1, stdout="", stderr="some error"),
        ]
        vr = VerifyResult(passed=False, command_results=results)
        assert "[FAIL]" in vr.failure_details
        assert "`b`" in vr.failure_details
        assert vr.metrics["pass_rate"] == 0.5

    def test_timeout_in_details(self):
        results = [
            CommandResult(
                command="slow", exit_code=-1, stdout="", stderr="", timed_out=True,
            ),
        ]
        vr = VerifyResult(passed=False, command_results=results)
        assert "[TIMEOUT]" in vr.failure_details

    def test_empty_results(self):
        vr = VerifyResult(passed=True, command_results=[])
        assert vr.metrics["pass_rate"] == 0.0
        assert vr.failure_details == ""


# ---------------------------------------------------------------------------
# run_verify_command integration tests
# ---------------------------------------------------------------------------


class TestRunVerifyCommand:
    @pytest.mark.asyncio
    async def test_successful_command(self, tmp_workdir):
        result = await run_verify_command("echo hello", cwd=tmp_workdir, timeout=10)
        assert result.passed is True
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_failing_command(self, tmp_workdir):
        result = await run_verify_command(
            "exit 1", cwd=tmp_workdir, timeout=10,
        )
        assert result.passed is False
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_command_timeout(self, tmp_workdir):
        result = await run_verify_command(
            "sleep 60", cwd=tmp_workdir, timeout=0.1,
        )
        assert result.passed is False
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_command_with_stderr(self, tmp_workdir):
        result = await run_verify_command(
            "echo err >&2 && exit 2", cwd=tmp_workdir, timeout=10,
        )
        assert result.passed is False
        assert result.exit_code == 2
        assert "err" in result.stderr

    @pytest.mark.asyncio
    async def test_nonexistent_command(self, tmp_workdir):
        result = await run_verify_command(
            "nonexistent_cmd_12345", cwd=tmp_workdir, timeout=5,
        )
        assert result.passed is False


# ---------------------------------------------------------------------------
# run_verification integration tests
# ---------------------------------------------------------------------------


class TestRunVerification:
    @pytest.mark.asyncio
    async def test_all_pass_criteria(self, tmp_workdir):
        result = await run_verification(
            ["echo a", "echo b"], cwd=tmp_workdir,
            success_criteria="all_pass",
        )
        assert result.passed is True
        assert result.metrics["passed_commands"] == 2

    @pytest.mark.asyncio
    async def test_all_pass_one_fails(self, tmp_workdir):
        result = await run_verification(
            ["echo a", "exit 1"], cwd=tmp_workdir,
            success_criteria="all_pass",
        )
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_any_pass_criteria(self, tmp_workdir):
        result = await run_verification(
            ["exit 1", "echo ok"], cwd=tmp_workdir,
            success_criteria="any_pass",
        )
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_any_pass_all_fail(self, tmp_workdir):
        result = await run_verification(
            ["exit 1", "exit 2"], cwd=tmp_workdir,
            success_criteria="any_pass",
        )
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_empty_commands(self, tmp_workdir):
        result = await run_verification(
            [], cwd=tmp_workdir,
        )
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_summary_includes_failures(self, tmp_workdir):
        result = await run_verification(
            ["echo ok", "exit 1"], cwd=tmp_workdir,
        )
        assert "失败" in result.summary


# ---------------------------------------------------------------------------
# build_fix_prompt tests
# ---------------------------------------------------------------------------


class TestBuildFixPrompt:
    def test_basic_prompt(self):
        vr = VerifyResult(
            passed=False,
            command_results=[
                CommandResult(
                    command="pytest", exit_code=1,
                    stdout="FAILED test_foo.py", stderr="",
                ),
            ],
        )
        prompt = build_fix_prompt("coding", 1, 3, vr)
        assert "1/3" in prompt
        assert "pytest" in prompt
        assert "FAILED test_foo.py" in prompt
        assert "只修复" in prompt

    def test_prompt_with_timeout(self):
        vr = VerifyResult(
            passed=False,
            command_results=[
                CommandResult(
                    command="slow_test", exit_code=-1,
                    stdout="", stderr="", timed_out=True,
                ),
            ],
        )
        prompt = build_fix_prompt("test", 2, 5, vr)
        assert "TIMEOUT" in prompt
