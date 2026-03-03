"""Tests for core engine functions: circuit breaker, task claim, state transitions, gates."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db.session import async_session_factory
from app.models.task import TaskModel
from app.worker import engine


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_stage(**kw) -> SimpleNamespace:
    defaults = dict(
        id="stage-1",
        stage_name="coding",
        agent_role="coding",
        error_message=None,
        output_summary=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_task(**kw) -> SimpleNamespace:
    defaults = dict(
        id="task-1",
        title="Test Task",
        status="running",
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)




@pytest.mark.asyncio
async def test_circuit_breaker_not_triggered(monkeypatch):
    """Normal usage well below limits → returns False, task not failed."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "CB_MAX_TOKENS_PER_TASK", 200_000)
    monkeypatch.setattr(engine.settings, "CB_MAX_COST_PER_TASK_RMB", 50.0)

    task = _make_task(total_tokens=100, total_cost_rmb=1.0)
    stage = _make_stage()
    session = SimpleNamespace(add=lambda x: None, commit=AsyncMock())

    result = await engine._check_circuit_breaker(session, task, stage)  # type: ignore[arg-type]
    assert result is False
    fail_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_circuit_breaker_token_limit_exceeded(monkeypatch):
    """Token limit exceeded → _fail_task called, returns True."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "CB_MAX_TOKENS_PER_TASK", 100)
    monkeypatch.setattr(engine.settings, "CB_MAX_COST_PER_TASK_RMB", 999.0)

    task = _make_task(total_tokens=999_999, total_cost_rmb=0.1)
    stage = _make_stage()
    session = SimpleNamespace(add=lambda x: None, commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._check_circuit_breaker(session, task, stage)  # type: ignore[arg-type]

    assert result is True
    fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_circuit_breaker_cost_limit_exceeded(monkeypatch):
    """Cost limit exceeded → _fail_task called, returns True."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "CB_MAX_TOKENS_PER_TASK", 999_999_999)
    monkeypatch.setattr(engine.settings, "CB_MAX_COST_PER_TASK_RMB", 1.0)

    task = _make_task(total_tokens=0, total_cost_rmb=999.0)
    stage = _make_stage()
    session = SimpleNamespace(add=lambda x: None, commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._check_circuit_breaker(session, task, stage)  # type: ignore[arg-type]

    assert result is True
    fail_task.assert_awaited_once()
    # Verify reason contains cost info
    call_reason = fail_task.call_args[0][2]
    assert "cost" in call_reason.lower() or "¥" in call_reason


# ── 2. _pick_pending_task ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_task_sets_status_and_broadcasts(monkeypatch):
    """_fail_task sets status=failed, broadcasts, calls notify."""
    task_id = "tt-fail-task-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Fail Me", status="running"))
        await session.commit()

    broadcast = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(engine, "_safe_broadcast", broadcast)
    monkeypatch.setattr(engine, "notify_task_failed", notify)
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        await engine._fail_task(session, task, "something went wrong")  # type: ignore[arg-type]

    # Verify DB state
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.completed_at is not None

    broadcast.assert_awaited()
    notify.assert_awaited_once()

    # Cleanup
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_fail_task_keeps_cancelled_status(monkeypatch):
    """_fail_task should not override externally cancelled tasks."""
    task_id = "tt-fail-task-cancelled-guard-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Already Cancelled", status="cancelled"))
        await session.commit()

    broadcast = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(engine, "_safe_broadcast", broadcast)
    monkeypatch.setattr(engine, "notify_task_failed", notify)
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        await engine._fail_task(session, task, "should be ignored")  # type: ignore[arg-type]

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        assert task.status == "cancelled"

    broadcast.assert_not_awaited()
    notify.assert_not_awaited()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_complete_task_sets_status_and_broadcasts(monkeypatch):
    """_complete_task sets status=completed, broadcasts, calls notify."""
    task_id = "tt-complete-task-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Complete Me", status="running", total_tokens=100))
        await session.commit()

    broadcast = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(engine, "_safe_broadcast", broadcast)
    monkeypatch.setattr(engine, "notify_task_completed", notify)
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        await engine._complete_task(session, task)  # type: ignore[arg-type]

    # Verify DB state
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        assert task.status == "completed"
        assert task.completed_at is not None

    broadcast.assert_awaited()
    notify.assert_awaited_once()

    # Cleanup
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


# ── 4. _handle_gate polling state machine ─────────────────────────────────


@pytest.mark.asyncio
async def test_poll_loop_fails_task_on_timeout(monkeypatch):
    """When _process_task exceeds timeout, _fail_task is called with timeout reason."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine.settings, "WORKER_TASK_TIMEOUT", 0.01)
    monkeypatch.setattr(engine.settings, "WORKER_POLL_INTERVAL", 999.0)  # Don't re-poll

    # _process_task will sleep longer than the timeout
    async def _slow_process(session, task):
        await asyncio.sleep(1.0)

    monkeypatch.setattr(engine, "_process_task", _slow_process)

    task_id = "tt-timeout-task-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Timeout Task", status="pending"))
        await session.commit()

    try:
        # Run only one iteration of the poll loop
        monkeypatch.setattr(engine, "_running", True)

        async def _one_shot_loop():
            try:
                async with async_session_factory() as session:
                    task = await engine._pick_pending_task(session)
                    if task:
                        try:
                            await asyncio.wait_for(
                                engine._process_task(session, task),  # type: ignore[arg-type]
                                timeout=engine.settings.WORKER_TASK_TIMEOUT,
                            )
                        except asyncio.TimeoutError:
                            await engine._fail_task(
                                session, task,
                                f"Task timed out after {engine.settings.WORKER_TASK_TIMEOUT:.0f}s",
                            )
                            from app.worker.agents import close_agents_for_task
                            close_agents_for_task(str(task.id))
            except Exception:
                pass

        with patch("app.worker.agents.close_agents_for_task"):
            await _one_shot_loop()

        fail_task.assert_awaited_once()
        call_reason = fail_task.call_args[0][2]
        assert "timed out" in call_reason.lower()
    finally:
        # Cleanup
        async with async_session_factory() as session:
            task = await session.get(TaskModel, task_id)
            if task:
                await session.delete(task)
            await session.commit()


# ── 7. _recover_stale_tasks ───────────────────────────────────────────────
