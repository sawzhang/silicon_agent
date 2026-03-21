"""Integration tests for the objective evaluator loop in executor.py."""
from types import SimpleNamespace

import pytest

from app.worker import executor, verifier
from app.worker.verifier import CommandResult, VerifyResult


def _make_stage(name="coding", role="coding"):
    return SimpleNamespace(
        id="stage-1",
        stage_name=name,
        agent_role=role,
        self_assessment_score=None,
    )


def _make_runner(responses):
    """Create a fake runner that returns responses in sequence."""
    call_count = 0

    async def chat(prompt, reset=False, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return SimpleNamespace(text_content=responses[idx])

    runner = SimpleNamespace(
        chat=chat,
        cumulative_usage=SimpleNamespace(total_tokens=100),
        config=SimpleNamespace(model="test-model"),
    )
    return runner


class TestRunObjectiveLoop:
    """Tests for _run_objective_loop."""

    @pytest.mark.asyncio
    async def test_passes_on_first_try(self, monkeypatch, tmp_path):
        """When verification passes immediately, no fix prompt is sent."""
        stage = _make_stage()
        runner = _make_runner(["initial output"])
        runtime_overrides = {"model": "test", "temperature": 0.7}
        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["echo ok"],
            "max_iterations": 3,
        }

        output, tokens = await executor._run_objective_loop(
            runner, "initial output", 100, stage, runtime_overrides,
            config, str(tmp_path),
        )

        assert output == "initial output"
        assert stage.self_assessment_score == 1.0

    @pytest.mark.asyncio
    async def test_fixes_after_failure(self, monkeypatch, tmp_path):
        """Agent gets a fix prompt after verification fails, then passes."""
        stage = _make_stage()

        verify_calls = []

        async def mock_verification(commands, cwd, success_criteria="all_pass", timeout=None):
            verify_calls.append(len(verify_calls))
            if len(verify_calls) == 1:
                return VerifyResult(
                    passed=False,
                    command_results=[
                        CommandResult(
                            command="pytest", exit_code=1,
                            stdout="FAILED test_x.py::test_a", stderr="",
                        ),
                    ],
                    summary="0/1 passed",
                )
            return VerifyResult(
                passed=True,
                command_results=[
                    CommandResult(command="pytest", exit_code=0, stdout="1 passed", stderr=""),
                ],
                summary="1/1 passed",
            )

        monkeypatch.setattr(verifier, "run_verification", mock_verification)
        monkeypatch.setattr(
            verifier, "build_fix_prompt",
            lambda *a, **kw: "fix the test",
        )
        monkeypatch.setattr(executor, "_chat_kwargs_for_runner", lambda r, o: {})

        runner = _make_runner(["fixed output"])
        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["pytest"],
            "max_iterations": 3,
        }

        output, tokens = await executor._run_objective_loop(
            runner, "initial output", 100, stage, {},
            config, str(tmp_path),
        )

        assert output == "fixed output"
        assert len(verify_calls) == 2
        assert stage.self_assessment_score == 1.0

    @pytest.mark.asyncio
    async def test_max_iterations_exhausted(self, monkeypatch, tmp_path):
        """Loop stops after max_iterations even if still failing."""
        async def always_fail(commands, cwd, success_criteria="all_pass", timeout=None):
            return VerifyResult(
                passed=False,
                command_results=[
                    CommandResult(command="ruff", exit_code=1, stdout="error", stderr=""),
                ],
            )

        monkeypatch.setattr(verifier, "run_verification", always_fail)
        monkeypatch.setattr(
            verifier, "build_fix_prompt", lambda *a, **kw: "fix it",
        )
        monkeypatch.setattr(executor, "_chat_kwargs_for_runner", lambda r, o: {})

        runner = _make_runner(["attempt 1", "attempt 2"])
        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["ruff check ."],
            "max_iterations": 2,
        }

        output, _ = await executor._run_objective_loop(
            runner, "initial", 100, _make_stage(), {},
            config, str(tmp_path),
        )

        # max_iterations=2: iter 1 fails → fix → iter 2 fails → break
        # After iter 1 fix, output becomes "attempt 1"
        assert output == "attempt 1"

    @pytest.mark.asyncio
    async def test_token_budget_stops_loop(self, monkeypatch, tmp_path):
        """Loop stops when token budget is exhausted."""
        stage = _make_stage()

        async def always_fail(commands, cwd, success_criteria="all_pass", timeout=None):
            return VerifyResult(
                passed=False,
                command_results=[
                    CommandResult(command="test", exit_code=1, stdout="fail", stderr=""),
                ],
            )

        monkeypatch.setattr(verifier, "run_verification", always_fail)
        monkeypatch.setattr(
            verifier, "build_fix_prompt", lambda *a, **kw: "fix it",
        )
        monkeypatch.setattr(executor, "_chat_kwargs_for_runner", lambda r, o: {})

        runner = SimpleNamespace(
            config=SimpleNamespace(model="test"),
            cumulative_usage=SimpleNamespace(total_tokens=99999),
        )

        async def heavy_chat(prompt, reset=False, **kwargs):
            runner.cumulative_usage.total_tokens += 60000
            return SimpleNamespace(text_content="attempted fix")

        runner.chat = heavy_chat

        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["test"],
            "max_iterations": 10,
            "token_budget": 10000,
        }

        output, _ = await executor._run_objective_loop(
            runner, "initial", 99999, stage, {},
            config, str(tmp_path),
        )

        # Iteration 1: verify fails → chat called (output becomes "attempted fix",
        # tokens jump by 60k). Iteration 2: verify fails → budget exceeded → break.
        # Output is from the last successful chat call.
        assert output == "attempted fix"

    @pytest.mark.asyncio
    async def test_no_commands_skips(self, tmp_path):
        """Loop is skipped when no commands are configured."""
        runner = _make_runner([])
        config = {
            "enabled": True,
            "type": "objective",
            "commands": [],
        }

        output, tokens = await executor._run_objective_loop(
            runner, "original", 50, _make_stage(), {},
            config, str(tmp_path),
        )

        assert output == "original"
        assert tokens == 50

    @pytest.mark.asyncio
    async def test_no_workdir_skips(self):
        """Loop is skipped when workdir is None."""
        runner = _make_runner([])
        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["echo test"],
        }

        output, tokens = await executor._run_objective_loop(
            runner, "original", 50, _make_stage(), {},
            config, None,
        )

        assert output == "original"

    @pytest.mark.asyncio
    async def test_any_pass_criteria(self, monkeypatch, tmp_path):
        """With any_pass, loop passes when at least one command succeeds."""
        stage = _make_stage()

        async def mixed_result(commands, cwd, success_criteria="all_pass", timeout=None):
            return VerifyResult(
                passed=(success_criteria == "any_pass"),
                command_results=[
                    CommandResult(command="lint", exit_code=1, stdout="", stderr="err"),
                    CommandResult(command="test", exit_code=0, stdout="ok", stderr=""),
                ],
            )

        monkeypatch.setattr(verifier, "run_verification", mixed_result)

        runner = _make_runner([])
        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["lint", "test"],
            "success_criteria": "any_pass",
            "max_iterations": 3,
        }

        output, _ = await executor._run_objective_loop(
            runner, "output", 100, stage, {},
            config, str(tmp_path),
        )

        assert output == "output"
        assert stage.self_assessment_score == 0.5

    @pytest.mark.asyncio
    async def test_chat_exception_breaks_loop(self, monkeypatch, tmp_path):
        """If agent chat raises, loop breaks gracefully."""
        stage = _make_stage()

        async def fail_verify(commands, cwd, success_criteria="all_pass", timeout=None):
            return VerifyResult(
                passed=False,
                command_results=[
                    CommandResult(command="test", exit_code=1, stdout="", stderr="err"),
                ],
            )

        monkeypatch.setattr(verifier, "run_verification", fail_verify)
        monkeypatch.setattr(
            verifier, "build_fix_prompt", lambda *a, **kw: "fix",
        )
        monkeypatch.setattr(executor, "_chat_kwargs_for_runner", lambda r, o: {})

        async def exploding_chat(prompt, reset=False, **kwargs):
            raise RuntimeError("LLM unavailable")

        runner = SimpleNamespace(
            config=SimpleNamespace(model="test"),
            cumulative_usage=SimpleNamespace(total_tokens=100),
            chat=exploding_chat,
        )

        config = {
            "enabled": True,
            "type": "objective",
            "commands": ["test"],
            "max_iterations": 5,
        }

        output, _ = await executor._run_objective_loop(
            runner, "original", 100, stage, {},
            config, str(tmp_path),
        )

        # Should return original output, not crash
        assert output == "original"
