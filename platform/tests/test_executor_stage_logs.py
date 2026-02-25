from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import executor


class _FakeEvents:
    def on(self, _event_name: str, _handler, source: str | None = None) -> None:
        return None

    def off_by_source(self, _source: str) -> None:
        return None


class _FakeRunner:
    def __init__(self) -> None:
        self.events = _FakeEvents()
        self.config = SimpleNamespace(model="test-model")
        self.cumulative_usage = SimpleNamespace(total_tokens=321)

    def reset_usage(self) -> None:
        return None

    async def chat(self, _prompt: str, reset: bool = True):
        return SimpleNamespace(text_content="stage output")


@pytest.mark.asyncio
async def test_execute_stage_logs_use_event_timestamps(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id="task-1",
        title="task title",
        description="task description",
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id="stage-1",
        stage_name="doc",
        agent_role="doc",
        status="pending",
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )

    monkeypatch.setattr(executor, "_get_agent", AsyncMock(return_value=None))
    monkeypatch.setattr(executor, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(executor, "build_user_prompt", lambda _ctx: "prompt")
    monkeypatch.setattr(
        executor,
        "get_agent",
        lambda _role, _task_id, model=None, max_turns=None, extra_skill_dirs=None, system_prompt_append=None: _FakeRunner(),
    )

    captured_logs: list[dict] = []

    async def _capture_logs(_self, logs):
        captured_logs.extend(logs)

    monkeypatch.setattr(executor.TaskLogService, "append_logs", _capture_logs)

    result = await executor.execute_stage(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
    )

    assert result == "stage output"
    assert len(captured_logs) >= 2

    llm_request = next(log for log in captured_logs if log["event_type"] == "llm_request_sent")
    llm_response = next(log for log in captured_logs if log["event_type"] == "llm_response_received")

    assert isinstance(llm_request["created_at"], datetime)
    assert isinstance(llm_response["created_at"], datetime)
    assert llm_request["created_at"] <= llm_response["created_at"]


@pytest.mark.asyncio
async def test_execute_stage_uses_agent_config_runtime_overrides(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id="task-2",
        title="task title",
        description="task description",
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id="stage-2",
        stage_name="coding",
        agent_role="coding",
        status="pending",
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )
    db_agent = SimpleNamespace(
        role="coding",
        model_name="gpt-5.1-codex-mini",
        config={
            "max_turns": 18,
            "extra_skill_dirs": ["/tmp/skills"],
            "system_prompt_append": "extra prompt",
            "temperature": 0.2,
            "max_tokens": 1200,
        },
    )

    monkeypatch.setattr(executor, "_get_agent", AsyncMock(return_value=db_agent))
    monkeypatch.setattr(executor, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(executor, "build_user_prompt", lambda _ctx: "prompt")
    captured_params: dict[str, object] = {}

    class _ChatRunner(_FakeRunner):
        async def chat(self, _prompt: str, reset: bool = True, temperature=None, max_tokens=None):
            captured_params["temperature"] = temperature
            captured_params["max_tokens"] = max_tokens
            return SimpleNamespace(text_content="stage output")

    def _capture_runner(_role, _task_id, model=None, max_turns=None, extra_skill_dirs=None, system_prompt_append=None):
        captured_params["model"] = model
        captured_params["max_turns"] = max_turns
        captured_params["extra_skill_dirs"] = extra_skill_dirs
        captured_params["system_prompt_append"] = system_prompt_append
        return _ChatRunner()

    monkeypatch.setattr(executor, "get_agent", _capture_runner)
    monkeypatch.setattr(executor.TaskLogService, "append_logs", AsyncMock())

    result = await executor.execute_stage(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
    )

    assert result == "stage output"
    assert captured_params["model"] == "gpt-5.1-codex-mini"
    assert captured_params["max_turns"] == 18
    assert captured_params["extra_skill_dirs"] == ["/tmp/skills"]
    assert captured_params["system_prompt_append"] == "extra prompt"
    assert captured_params["temperature"] == 0.2
    assert captured_params["max_tokens"] == 1200
