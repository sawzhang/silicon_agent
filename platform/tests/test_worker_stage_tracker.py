from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.worker.stage_tracker import (
    StageEventTracker,
    _append_output_summary,
    infer_tool_status,
    summarize_tool_command,
)


class _EventBus:
    def __init__(self):
        self._handlers: dict[str, list[tuple[object, str | None]]] = {}

    def on(self, name: str, handler, source: str | None = None) -> None:
        self._handlers.setdefault(name, []).append((handler, source))

    def off_by_source(self, source: str) -> None:
        for name, handlers in list(self._handlers.items()):
            self._handlers[name] = [(h, s) for h, s in handlers if s != source]

    async def emit(self, name: str, event) -> None:
        for handler, _ in list(self._handlers.get(name, [])):
            await handler(event)


class _BrokenEventBus(_EventBus):
    def off_by_source(self, source: str) -> None:  # pragma: no cover - intentionally throws
        raise RuntimeError("detach failed")


class _PipelineStub:
    def __init__(self):
        self.created: list[dict] = []
        self.updated: list[dict] = []

    async def emit_create(self, **kwargs):
        self.created.append(kwargs)
        return f"log-{len(self.created)}"

    async def emit_update(self, *, log_id: str, updates: dict, priority: str = "normal"):
        self.updated.append({"log_id": log_id, "updates": updates, "priority": priority})


@pytest.mark.parametrize(
    ("tool_name", "args", "expected"),
    [
        ("execute", {"command": "ls -la"}, "ls -la"),
        ("execute_script", {}, "execute_script"),
        ("read", {"path": "README.md"}, "read README.md"),
        ("write", {"path": "a.txt"}, "write a.txt"),
        ("skill", {"name": "lint"}, "skill:lint"),
        ("skill", {}, "skill"),
        ("unknown", {}, "unknown"),
        ("", {}, "tool"),
    ],
)
def test_summarize_tool_command(tool_name: str, args: dict, expected: str):
    assert summarize_tool_command(tool_name, args) == expected


def test_infer_tool_status_and_append_output_summary():
    assert infer_tool_status("Error: boom") == "failed"
    assert infer_tool_status("ok") == "success"

    merged, truncated = _append_output_summary("", "abc")
    assert merged == "abc"
    assert truncated is False

    long_chunk = "x" * 60_000
    merged2, truncated2 = _append_output_summary("", long_chunk)
    assert merged2.endswith("\n...[truncated]")
    assert truncated2 is True

    merged3, truncated3 = _append_output_summary(merged2, "new")
    assert merged3 == merged2
    assert truncated3 is True


@pytest.mark.asyncio
async def test_stage_tracker_chat_turn_tool_flow():
    pipeline = _PipelineStub()
    broadcast_calls: list[tuple[str, dict]] = []

    async def _broadcast(event_name: str, payload: dict) -> None:
        broadcast_calls.append((event_name, payload))

    tracker = StageEventTracker(
        task_id="task-1",
        stage_id="stage-1",
        stage_name="code",
        agent_role="coding",
        pipeline=pipeline,
        broadcast_fn=_broadcast,
    )

    runner = SimpleNamespace(events=_EventBus(), default_cwd="/tmp/work")

    tracker.register_runner(runner)
    tracker.register_runner(runner)  # idempotent registration

    correlation = await tracker.emit_chat_sent(prompt="hello", metadata={"attempt": 1})
    await runner.events.emit("turn_start", SimpleNamespace(turn=1, message_count=2))
    await runner.events.emit(
        "turn_end",
        SimpleNamespace(turn=1, has_tool_calls=True, tool_call_count=1, content="done"),
    )

    await runner.events.emit(
        "before_tool_call",
        SimpleNamespace(tool_call_id="tc-1", tool_name="read", args={"path": "README.md"}),
    )
    await runner.events.emit(
        "tool_execution_update",
        SimpleNamespace(tool_call_id="tc-1", output="chunk-output"),
    )
    await runner.events.emit(
        "after_tool_result",
        SimpleNamespace(
            tool_call_id="tc-1",
            tool_name="read",
            args={"path": "README.md"},
            result="ok-result",
        ),
    )

    await tracker.emit_chat_received(
        correlation,
        status="success",
        response_body={"content": "ok"},
        duration_ms=1.23,
    )

    event_types = [item["event_type"] for item in pipeline.created]
    assert "agent_runner_chat_sent" in event_types
    assert "agent_runner_chat_received" in event_types
    assert "llm_turn_sent" in event_types
    assert "llm_turn_received" in event_types
    assert "tool_call_executed" in event_types
    assert any(item["updates"].get("status") == "success" for item in pipeline.updated)

    assert len(broadcast_calls) >= 2
    assert any(call[1].get("finished") is False for call in broadcast_calls)
    assert any(call[1].get("finished") is True for call in broadcast_calls)


@pytest.mark.asyncio
async def test_stage_tracker_after_tool_result_without_start_and_missing_workspace():
    pipeline = _PipelineStub()

    async def _broadcast(_event_name: str, _payload: dict) -> None:
        return None

    tracker = StageEventTracker(
        task_id="task-2",
        stage_id="stage-2",
        stage_name="test",
        agent_role="test",
        pipeline=pipeline,
        broadcast_fn=_broadcast,
    )

    runner = SimpleNamespace(events=_EventBus(), default_cwd=None)
    tracker.register_runner(runner)

    await runner.events.emit(
        "after_tool_result",
        SimpleNamespace(tool_call_id="tc-miss", tool_name="execute", args={}, result="Error: boom"),
    )

    created = pipeline.created[-1]
    assert created["status"] == "failed"
    assert created["missing_fields"] == ["workspace"]


@pytest.mark.asyncio
async def test_stage_tracker_finalize_unfinished_and_detach_handles_errors():
    pipeline = _PipelineStub()
    broadcasts: list[dict] = []

    async def _broadcast(_event_name: str, payload: dict) -> None:
        broadcasts.append(payload)

    tracker = StageEventTracker(
        task_id="task-3",
        stage_id="stage-3",
        stage_name="review",
        agent_role="review",
        pipeline=pipeline,
        broadcast_fn=_broadcast,
    )

    tracker._turn_runs = {"turn-1": {"log_id": "l1", "started": time.monotonic() - 0.01}}
    tracker._chat_runs = {"chat-1": {"log_id": "l2", "started": time.monotonic() - 0.01}}
    tracker._tool_runs = {
        "tool-1": {
            "log_id": "l3",
            "started": time.monotonic() - 0.01,
            "summary": "partial",
            "truncated": True,
        }
    }

    tracker._instrumented_runners = [SimpleNamespace(events=_BrokenEventBus())]

    tracker.detach_all_handlers()
    await tracker.finalize_unfinished(status="cancelled", reason="stopped")

    assert len(pipeline.updated) == 3
    assert any(item["updates"].get("output_truncated") is True for item in pipeline.updated)
    assert any(item.get("status") == "cancelled" for item in broadcasts)


@pytest.mark.asyncio
async def test_emit_system_event_is_forwarded_to_pipeline():
    pipeline = _PipelineStub()

    async def _broadcast(_event_name: str, _payload: dict) -> None:
        return None

    tracker = StageEventTracker(
        task_id="task-4",
        stage_id="stage-4",
        stage_name="parse",
        agent_role="orchestrator",
        pipeline=pipeline,
        broadcast_fn=_broadcast,
    )

    await tracker.emit_system_event(
        "sandbox_create_started",
        status="running",
        response_body={"workspace": "/tmp/x"},
        result="started",
        priority="high",
    )

    assert pipeline.created[-1]["event_type"] == "sandbox_create_started"
