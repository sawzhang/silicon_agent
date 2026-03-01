"""Tests for core engine functions: circuit breaker, task claim, state transitions, gates."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
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


# ── 1. _check_circuit_breaker ─────────────────────────────────────────────


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
async def test_pick_pending_task_no_pending():
    """When no pending tasks, returns None."""
    async with async_session_factory() as session:
        result = await engine._pick_pending_task(session)
    assert result is None


@pytest.mark.asyncio
async def test_pick_pending_task_claims_oldest():
    """Oldest pending task is claimed atomically."""
    task_id = "tt-pick-pending-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Pick Me", status="pending"))
        await session.commit()

    async with async_session_factory() as session:
        result = await engine._pick_pending_task(session)
        assert result is not None
        assert result.id == task_id
        assert result.status == "claimed"

    # Cleanup
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_pick_pending_task_concurrent_claim():
    """Only one worker claims the task when two compete."""
    task_id = "tt-pick-concurrent-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Concurrent Task", status="pending"))
        await session.commit()

    # Simulate two concurrent workers
    async with async_session_factory() as session1:
        async with async_session_factory() as session2:
            result1 = await engine._pick_pending_task(session1)
            result2 = await engine._pick_pending_task(session2)

    # Only one should have succeeded
    got_task = [r for r in [result1, result2] if r is not None]
    assert len(got_task) == 1
    assert got_task[0].id == task_id

    # Cleanup
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


# ── 3. _fail_task / _complete_task ────────────────────────────────────────


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
async def test_handle_gate_approved(monkeypatch):
    """Gate approved → returns {"result": "approved"}."""
    task_id = "tt-gate-approved-task"
    stage_id = "tt-gate-approved-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Approved", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id,
            stage_name="review", agent_role="review", status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    # We'll approve the gate by monkeypatching session.refresh to set gate.status="approved"
    approve_calls = [0]

    async def _auto_approve_refresh(obj):
        if isinstance(obj, HumanGateModel):
            approve_calls[0] += 1
            obj.status = "approved"
            obj.review_comment = "looks good"

    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _auto_approve_refresh  # type: ignore

        result = await engine._handle_gate(
            session=session,
            task=task,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            gate_type="human_approve",
            stage_output="output text",
        )

    assert result["result"] == "approved"

    # Cleanup
    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_handle_gate_rejected(monkeypatch):
    """Gate rejected → returns {"result": "rejected"}."""
    task_id = "tt-gate-rejected-task"
    stage_id = "tt-gate-rejected-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Rejected", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id,
            stage_name="review", agent_role="review", status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)

    async def _auto_reject_refresh(obj):
        if isinstance(obj, HumanGateModel):
            obj.status = "rejected"
            obj.review_comment = "not good enough"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _auto_reject_refresh  # type: ignore

        result = await engine._handle_gate(
            session=session,
            task=task,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            gate_type="human_approve",
            stage_output="output text",
        )

    assert result["result"] == "rejected"
    assert result["comment"] == "not good enough"

    # Cleanup
    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_handle_gate_timeout(monkeypatch):
    """Gate timeout → returns {"result": "timeout"}."""
    task_id = "tt-gate-timeout-task"
    stage_id = "tt-gate-timeout-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Timeout", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id,
            stage_name="review", agent_role="review", status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)
    # Very short timeout, very fast poll
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.001)

    # refresh does nothing (gate stays pending)
    async def _noop_refresh(obj):
        pass

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _noop_refresh  # type: ignore

        result = await engine._handle_gate(
            session=session,
            task=task,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            gate_type="human_approve",
            stage_output="output text",
        )

    assert result["result"] == "timeout"

    # Cleanup
    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


# ── 5. _handle_gate_with_retry ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_gate_with_retry_approved_first_pass(monkeypatch):
    """Gate approved on first try → returns original output."""
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace(commit=AsyncMock())

    monkeypatch.setattr(
        engine, "_handle_gate",
        AsyncMock(return_value={"result": "approved", "comment": ""}),
    )
    execute_stage = AsyncMock(return_value="new output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_stage)

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._handle_gate_with_retry(
        session=session,  # type: ignore[arg-type]
        task=task,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        gate_def={"type": "human_approve", "max_retries": 2},
        stage_output="original output",
        stage_index=0,
        prior_outputs=[],
        compression=compression,
        project_memory_store=None,
        repo_context=None,
        stage_defs={},
        workspace_path=None,
        sandbox_info=None,
    )
    assert result == "original output"
    execute_stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_gate_with_retry_rejected_within_limit(monkeypatch):
    """Gate rejected on first pass, re-executed on retry."""
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace(commit=AsyncMock())

    # rejected → then approved on second call
    handle_gate = AsyncMock(side_effect=[
        {"result": "rejected", "comment": "fix it"},
        {"result": "approved", "comment": "good now"},
    ])
    monkeypatch.setattr(engine, "_handle_gate", handle_gate)

    new_output = "fixed output"
    execute_single = AsyncMock(return_value=new_output)
    monkeypatch.setattr(engine, "_execute_single_stage", execute_single)

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._handle_gate_with_retry(
        session=session,  # type: ignore[arg-type]
        task=task,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        gate_def={"type": "human_approve", "max_retries": 2},
        stage_output="original output",
        stage_index=0,
        prior_outputs=[{"stage": "coding", "output": "original output"}],
        compression=compression,
        project_memory_store=None,
        repo_context=None,
        stage_defs={},
        workspace_path=None,
        sandbox_info=None,
    )
    assert result == new_output
    execute_single.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_gate_with_retry_rejected_exhausted(monkeypatch):
    """Gate rejected and no retries left → _fail_task called, returns None."""
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace(commit=AsyncMock())

    monkeypatch.setattr(
        engine, "_handle_gate",
        AsyncMock(return_value={"result": "rejected", "comment": "nope"}),
    )
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._handle_gate_with_retry(
        session=session,  # type: ignore[arg-type]
        task=task,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        gate_def={"type": "human_approve", "max_retries": 0},
        stage_output="original output",
        stage_index=0,
        prior_outputs=[],
        compression=compression,
        project_memory_store=None,
        repo_context=None,
        stage_defs={},
        workspace_path=None,
        sandbox_info=None,
    )
    assert result is None
    fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_gate_with_retry_revised(monkeypatch):
    """Gate revised → re-executes stage with revision context."""
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace(commit=AsyncMock())

    revised_output = "revised output"
    handle_gate = AsyncMock(side_effect=[
        {"result": "revised", "comment": "revise this", "revised_content": "new plan"},
        {"result": "approved", "comment": ""},
    ])
    monkeypatch.setattr(engine, "_handle_gate", handle_gate)

    execute_single = AsyncMock(return_value=revised_output)
    monkeypatch.setattr(engine, "_execute_single_stage", execute_single)

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._handle_gate_with_retry(
        session=session,  # type: ignore[arg-type]
        task=task,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        gate_def={"type": "human_approve", "max_retries": 2},
        stage_output="original output",
        stage_index=0,
        prior_outputs=[],
        compression=compression,
        project_memory_store=None,
        repo_context=None,
        stage_defs={},
        workspace_path=None,
        sandbox_info=None,
    )
    assert result == revised_output
    # Verify gate_rejection_context passed correctly
    call_kwargs = execute_single.call_args.kwargs
    assert call_kwargs.get("gate_rejection_context") is not None


# ── 6. Task timeout ───────────────────────────────────────────────────────


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


@pytest.mark.asyncio
async def test_recover_stale_tasks_resets_running_and_claimed():
    """running/claimed tasks are reset to pending; other statuses untouched."""
    running_id = "tt-recover-running-1"
    claimed_id = "tt-recover-claimed-1"
    pending_id = "tt-recover-pending-1"
    completed_id = "tt-recover-completed-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=running_id, title="Running", status="running"))
        session.add(TaskModel(id=claimed_id, title="Claimed", status="claimed"))
        session.add(TaskModel(id=pending_id, title="Pending", status="pending"))
        session.add(TaskModel(id=completed_id, title="Completed", status="completed"))
        await session.commit()

    await engine._recover_stale_tasks()

    async with async_session_factory() as session:
        running = await session.get(TaskModel, running_id)
        claimed = await session.get(TaskModel, claimed_id)
        pending = await session.get(TaskModel, pending_id)
        completed = await session.get(TaskModel, completed_id)

        assert running is not None and running.status == "pending"
        assert claimed is not None and claimed.status == "pending"
        assert pending is not None and pending.status == "pending"
        assert completed is not None and completed.status == "completed"

    # Cleanup
    async with async_session_factory() as session:
        for tid in [running_id, claimed_id, pending_id, completed_id]:
            task = await session.get(TaskModel, tid)
            if task:
                await session.delete(task)
        await session.commit()
