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
    monkeypatch.setattr(executor, "get_agent", lambda _role, _task_id: _FakeRunner())

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

