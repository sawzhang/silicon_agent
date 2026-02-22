"""Unit tests for worker engine pure functions."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.engine import _parse_gates, _sort_stages, _build_repo_context
from app.worker.engine import _safe_broadcast as engine_safe_broadcast
from app.worker.executor import _safe_broadcast as executor_safe_broadcast


def _make_task(template=None, stages=None):
    """Create a mock TaskModel with optional template and stages."""
    task = MagicMock()
    task.template = template
    task.stages = stages or []
    return task


def _make_template(stages_json=None, gates_json=None):
    """Create a mock TaskTemplateModel with stages/gates JSON strings."""
    tmpl = MagicMock()
    tmpl.stages = stages_json
    tmpl.gates = gates_json
    return tmpl


def _make_stage(name, order=None):
    """Create a mock TaskStageModel."""
    stage = MagicMock()
    stage.stage_name = name
    stage.id = f"stage-{name}"
    return stage


class TestParseGates:
    def test_parse_gates_empty(self):
        """Task with no template returns empty dict."""
        task = _make_task(template=None)
        assert _parse_gates(task) == {}

    def test_parse_gates_with_template(self):
        """Task with template.gates returns gate mapping."""
        gates = [{"after_stage": "coding", "type": "human_approve"}]
        tmpl = _make_template(gates_json=json.dumps(gates))
        task = _make_task(template=tmpl)
        result = _parse_gates(task)
        assert result == {"coding": "human_approve"}


class TestSortStages:
    def test_sort_stages_by_order(self):
        """Stages are sorted by template-defined order."""
        stage_defs = [
            {"name": "test", "order": 2},
            {"name": "coding", "order": 1},
            {"name": "review", "order": 3},
        ]
        tmpl = _make_template(stages_json=json.dumps(stage_defs))

        coding = _make_stage("coding")
        test = _make_stage("test")
        review = _make_stage("review")

        task = _make_task(template=tmpl, stages=[review, coding, test])
        sorted_stages = _sort_stages(task)
        names = [s.stage_name for s in sorted_stages]
        assert names == ["coding", "test", "review"]

    def test_sort_stages_no_template(self):
        """Task with no template returns stages in original order."""
        s1 = _make_stage("a")
        s2 = _make_stage("b")
        s3 = _make_stage("c")
        task = _make_task(template=None, stages=[s1, s2, s3])
        result = _sort_stages(task)
        names = [s.stage_name for s in result]
        assert names == ["a", "b", "c"]


class TestBuildRepoContext:
    def test_build_repo_context(self):
        """Project with tech_stack/repo_tree/repo_url produces formatted string."""
        project = MagicMock()
        project.tech_stack = ["Python", "FastAPI"]
        project.repo_tree = "src/\n  main.py\n  utils.py"
        project.repo_url = "https://github.com/example/repo"
        project.branch = "develop"

        result = _build_repo_context(project)
        assert "Python" in result
        assert "FastAPI" in result
        assert "src/" in result
        assert "main.py" in result
        assert "https://github.com/example/repo" in result
        assert "develop" in result

    def test_build_repo_context_default_branch(self):
        """Project with no branch defaults to main."""
        project = MagicMock()
        project.tech_stack = None
        project.repo_tree = None
        project.repo_url = "https://github.com/example/repo"
        project.branch = None

        result = _build_repo_context(project)
        assert "main" in result


class TestSafeBroadcast:
    @pytest.mark.asyncio
    async def test_engine_safe_broadcast_swallows_errors(self):
        """engine._safe_broadcast catches ws_manager errors."""
        with patch("app.worker.engine.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock(side_effect=RuntimeError("ws down"))
            # Should NOT raise
            await engine_safe_broadcast("test_event", {"key": "val"})
            mock_ws.broadcast.assert_called_once_with("test_event", {"key": "val"})

    @pytest.mark.asyncio
    async def test_executor_safe_broadcast_swallows_errors(self):
        """executor._safe_broadcast catches ws_manager errors."""
        with patch("app.worker.executor.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock(side_effect=ConnectionError("closed"))
            await executor_safe_broadcast("stage_event", {"id": "1"})
            mock_ws.broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_safe_broadcast_success(self):
        """_safe_broadcast delegates to ws_manager on success."""
        with patch("app.worker.engine.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            await engine_safe_broadcast("ok_event", {"a": 1})
            mock_ws.broadcast.assert_called_once_with("ok_event", {"a": 1})

    def test_parse_gates_invalid_json_swallowed(self):
        """_parse_gates returns empty dict on invalid JSON (no crash)."""
        tmpl = _make_template(gates_json="not valid json")
        task = _make_task(template=tmpl)
        assert _parse_gates(task) == {}


class TestStageTimeout:
    @pytest.mark.asyncio
    async def test_wait_for_cancels_on_timeout(self):
        """Verify asyncio.wait_for raises TimeoutError for stuck coroutines.

        This validates the pattern used in executor.py to timeout hung LLM calls.
        """
        async def stuck_forever():
            await asyncio.sleep(3600)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(stuck_forever(), timeout=0.01)
