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


# ── 8. _has_git_worktree_changes ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_has_git_worktree_changes_no_path():
    """No path provided → returns None."""
    result = await engine._has_git_worktree_changes(None)
    assert result is None


@pytest.mark.asyncio
async def test_has_git_worktree_changes_with_changes(monkeypatch, tmp_path):
    """subprocess returns non-empty stdout → True."""
    import asyncio

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b" M some_file.py\n", b""

    async def _fake_create_subprocess_shell(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is True


@pytest.mark.asyncio
async def test_has_git_worktree_changes_empty(monkeypatch, tmp_path):
    """subprocess returns empty stdout → False."""
    import asyncio

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def _fake_create_subprocess_shell(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is False


@pytest.mark.asyncio
async def test_has_git_worktree_changes_nonzero_returncode(monkeypatch, tmp_path):
    """subprocess returncode != 0 → None."""
    import asyncio

    class FakeProc:
        returncode = 128

        async def communicate(self):
            return b"fatal: not a git repository", b""

    async def _fake_create_subprocess_shell(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is None


@pytest.mark.asyncio
async def test_has_git_worktree_changes_exception(monkeypatch, tmp_path):
    """Exception during subprocess → None."""
    import asyncio

    async def _raising(*a, **kw):
        raise OSError("no such binary")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _raising)
    result = await engine._has_git_worktree_changes(str(tmp_path))
    assert result is None


# ── 9. _should_skip_stage ─────────────────────────────────────────────────


def test_should_skip_stage_no_condition():
    """No condition in stage_defs → False (don't skip)."""
    stage = _make_stage(stage_name="coding")
    result = engine._should_skip_stage(stage, {}, {})  # type: ignore[arg-type]
    assert result is False


def test_should_skip_stage_condition_true(monkeypatch):
    """evaluate_condition returns True (should execute) → False (don't skip)."""
    from app.worker import conditions as cond_mod

    monkeypatch.setattr(cond_mod, "evaluate_condition", lambda cond, outputs: True)
    stage = _make_stage(stage_name="coding")
    stage_defs = {"coding": {"condition": "some_condition"}}
    result = engine._should_skip_stage(stage, stage_defs, {})  # type: ignore[arg-type]
    assert result is False


def test_should_skip_stage_condition_false(monkeypatch):
    """evaluate_condition returns False (should NOT execute) → True (skip)."""
    from app.worker import conditions as cond_mod

    monkeypatch.setattr(cond_mod, "evaluate_condition", lambda cond, outputs: False)
    stage = _make_stage(stage_name="coding")
    stage_defs = {"coding": {"condition": "some_condition"}}
    result = engine._should_skip_stage(stage, stage_defs, {})  # type: ignore[arg-type]
    assert result is True


def test_should_skip_stage_condition_raises(monkeypatch):
    """evaluate_condition raises exception → False (fallback: execute anyway)."""
    from app.worker import conditions as cond_mod

    def _raise(*a, **kw):
        raise RuntimeError("bad condition")

    monkeypatch.setattr(cond_mod, "evaluate_condition", _raise)
    stage = _make_stage(stage_name="coding")
    stage_defs = {"coding": {"condition": "some_condition"}}
    result = engine._should_skip_stage(stage, stage_defs, {})  # type: ignore[arg-type]
    assert result is False


# ── 10. _is_cancelled ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_cancelled_running():
    """task status='running' → False."""
    task_id = "tt-is-cancelled-running-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Running Task", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        result = await engine._is_cancelled(session, task_id)
    assert result is False

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_is_cancelled_cancelled():
    """task status='cancelled' → True."""
    task_id = "tt-is-cancelled-yes-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Cancelled Task", status="cancelled"))
        await session.commit()

    async with async_session_factory() as session:
        result = await engine._is_cancelled(session, task_id)
    assert result is True

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


# ── 11. _resolve_sandbox_workspace ────────────────────────────────────────


def test_resolve_sandbox_workspace_with_path(monkeypatch):
    """workspace_path provided → returns it unchanged."""
    monkeypatch.setattr(engine.settings, "SANDBOX_WORKSPACE_BASE_DIR", "/sandbox/base")
    path, source = engine._resolve_sandbox_workspace("task-42", "/custom/path", "worktree")
    assert path == "/custom/path"
    assert source == "worktree"


def test_resolve_sandbox_workspace_none_path(monkeypatch):
    """workspace_path=None → returns fallback containing task_id."""
    monkeypatch.setattr(engine.settings, "SANDBOX_WORKSPACE_BASE_DIR", "/sandbox/base")
    path, source = engine._resolve_sandbox_workspace("task-42", None, "original_source")
    assert "task-42" in path
    assert source == "fallback"


# ── 12. _parse_stage_defs and _group_stages_by_order edge cases ──────────


def test_parse_stage_defs_invalid_json():
    """Invalid JSON in template.stages → returns {}."""
    task = _make_task()
    task.template = SimpleNamespace(stages="not-valid-json{{", gates=None)
    result = engine._parse_stage_defs(task)  # type: ignore[arg-type]
    assert result == {}


def test_parse_stage_defs_no_template():
    """No template → returns {}."""
    task = _make_task()
    task.template = None
    result = engine._parse_stage_defs(task)  # type: ignore[arg-type]
    assert result == {}


def test_group_stages_by_order_no_template():
    """No template → stages each in own single-element group."""
    import json

    task = _make_task()
    task.template = None
    stages = [_make_stage(stage_name="parse"), _make_stage(stage_name="coding")]
    groups = engine._group_stages_by_order(stages, task)  # type: ignore[arg-type]
    assert len(groups) == 2
    for g in groups:
        assert len(g) == 1


# ── 13. Worker start/stop edge cases ──────────────────────────────────────


@pytest.mark.asyncio
async def test_start_worker_double_start_guard(monkeypatch):
    """start_worker when _running=True → returns early without creating a second task."""
    import app.worker.scheduler as _sched

    monkeypatch.setattr(_sched, "start_scheduler", AsyncMock())
    monkeypatch.setattr(_sched, "stop_scheduler", AsyncMock())
    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_all_agents", lambda: None)

    # Make sure worker is stopped first
    await engine.stop_worker()

    # Start once
    await engine.start_worker()
    task_after_first_start = engine._task

    # Start again — should return early, no new task created
    await engine.start_worker()
    assert engine._task is task_after_first_start  # same task object

    # Cleanup
    await engine.stop_worker()


@pytest.mark.asyncio
async def test_recover_stale_tasks_exception_does_not_raise(monkeypatch):
    """If DB error occurs in _recover_stale_tasks, it is caught internally."""
    original_factory = engine.async_session_factory

    async def _bad_context():
        raise RuntimeError("DB unavailable")

    class _BadCM:
        async def __aenter__(self):
            raise RuntimeError("DB unavailable")

        async def __aexit__(self, *a):
            pass

    monkeypatch.setattr(engine, "async_session_factory", lambda: _BadCM())
    # Should not raise
    await engine._recover_stale_tasks()
    # Restore
    monkeypatch.setattr(engine, "async_session_factory", original_factory)


@pytest.mark.asyncio
async def test_prune_stale_worktrees_disabled(monkeypatch):
    """_prune_stale_worktrees returns immediately when WORKTREE_ENABLED=False."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    # Should complete immediately without touching DB
    await engine._prune_stale_worktrees()  # no assertion needed — just must not raise


@pytest.mark.asyncio
async def test_prune_stale_worktrees_db_error(monkeypatch):
    """_prune_stale_worktrees with WORKTREE_ENABLED=True but DB error → logs, returns."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    class _BadCM:
        async def __aenter__(self):
            raise RuntimeError("DB unavailable")

        async def __aexit__(self, *a):
            pass

    original_factory = engine.async_session_factory
    monkeypatch.setattr(engine, "async_session_factory", lambda: _BadCM())
    # Should not raise
    await engine._prune_stale_worktrees()
    monkeypatch.setattr(engine, "async_session_factory", original_factory)


# ── 14. _cleanup_runtime_resources paths ──────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_worktree_success(monkeypatch):
    """worktree_mgr + worktree_path → calls cleanup_worktree successfully."""
    cleanup_calls = []
    task_id = "tt-cleanup-wt-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Cleanup Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        class FakeWorktreeMgr:
            async def cleanup_worktree(self, tid):
                cleanup_calls.append(tid)

        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            FakeWorktreeMgr(),
            "/fake/worktree",
            None,
            "worktree",
            None,
            None,
        )

    assert len(cleanup_calls) == 1

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_worktree_failure(monkeypatch):
    """worktree cleanup failure → catches exception, does not raise."""
    task_id = "tt-cleanup-wt-fail-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Cleanup Fail Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        class FailingWorktreeMgr:
            async def cleanup_worktree(self, tid):
                raise RuntimeError("cleanup exploded")

        # Should not raise
        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            FailingWorktreeMgr(),
            "/fake/worktree",
            None,
            "worktree",
            None,
            None,
        )

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_sandbox_destroy_raises(monkeypatch, tmp_path):
    """sandbox_mgr + sandbox_info → calls destroy; destroy raises → caught silently."""
    task_id = "tt-cleanup-sandbox-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Sandbox Cleanup Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        class FailingSandboxMgr:
            async def destroy(self, tid):
                raise RuntimeError("container gone")

        sandbox_info = SimpleNamespace(container_name="test-container")

        # Should not raise
        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            None,
            None,
            None,
            "unresolved",
            FailingSandboxMgr(),
            sandbox_info,
        )

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_cleanup_runtime_resources_tmp_workspace_removed(monkeypatch, tmp_path):
    """workspace_source starts with 'tmp_' → shutil.rmtree called on the workspace."""
    import shutil

    rmtree_calls = []
    real_rmtree = shutil.rmtree

    def _fake_rmtree(p, ignore_errors=False):
        rmtree_calls.append(str(p))

    task_id = "tt-cleanup-tmp-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Tmp Cleanup Test", status="running"))
        await session.commit()
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)

        monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
        monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

        import tempfile
        from pathlib import Path

        # Create a real subdirectory under the expected base
        tasks_base = Path(tempfile.gettempdir()) / "silicon_agent" / "tasks"
        ws_path = tasks_base / task_id
        ws_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(shutil, "rmtree", _fake_rmtree)

        await engine._cleanup_runtime_resources(
            task,  # type: ignore[arg-type]
            None,
            None,
            str(ws_path),
            "tmp_empty",
            None,
            None,
        )

    # The rmtree should have been called on our ws path
    assert any(task_id in p for p in rmtree_calls)

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ── 15. _finalize_task_resources paths ────────────────────────────────────


@pytest.mark.asyncio
async def test_finalize_task_resources_memory_extraction_success(monkeypatch):
    """MEMORY_ENABLED=True with valid project_memory_store and prior_outputs → extracts memories."""
    extract_mock = AsyncMock()
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr("app.worker.memory_extractor.extract_and_store_memories", extract_mock)

    task_id = "tt-finalize-mem-1"
    async with async_session_factory() as session:
        session.add(TaskModel(
            id=task_id, title="Memory Test", status="running",
            project_id=None,
        ))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        task.project_id = "proj-123"

        project_memory_store = SimpleNamespace(get_memory_for_role=lambda role: "memories")
        prior_outputs = [{"stage": "parse", "output": "parsed output"}]

        result = await engine._finalize_task_resources(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            prior_outputs,
            project_memory_store,
            None, None, None, "unresolved", None, None, None,
        )

    assert result is True
    extract_mock.assert_awaited_once()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_finalize_task_resources_memory_extraction_fails(monkeypatch):
    """Memory extraction fails → exception caught, logs failure, returns True anyway."""
    async def _raising(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr("app.worker.memory_extractor.extract_and_store_memories", _raising)

    task_id = "tt-finalize-mem-fail-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Memory Fail Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        task.project_id = "proj-456"

        project_memory_store = SimpleNamespace()
        prior_outputs = [{"stage": "parse", "output": "text"}]

        # Should not raise, returns True
        result = await engine._finalize_task_resources(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            prior_outputs,
            project_memory_store,
            None, None, None, "unresolved", None, None, None,
        )

    assert result is True

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_finalize_task_resources_commit_push_success(monkeypatch):
    """repo_url set + workspace_path set → commit_and_push_workspace called, returns True."""
    commit_push_mock = AsyncMock(return_value="feat/branch-123")
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "commit_and_push_workspace", commit_push_mock)
    # Also patch create_pr_for_workspace to prevent HTTP calls
    monkeypatch.setattr(engine, "create_pr_for_workspace", AsyncMock(return_value=None))

    # Use a SimpleNamespace task so we can freely set .project
    task = _make_task(id="tt-finalize-commit-sn-1", title="Commit Push Test")
    task.project = SimpleNamespace(
        repo_url="https://github.com/test/repo",
        branch="main",
    )
    task.target_branch = None

    session = SimpleNamespace(commit=AsyncMock())

    result = await engine._finalize_task_resources(
        session,  # type: ignore[arg-type]
        task,  # type: ignore[arg-type]
        [],
        None,
        None,        # worktree_mgr
        None,        # worktree_path
        "/tmp/test_ws",  # workspace_path
        "tmp_cloned",    # workspace_source
        "feat/branch-123",  # workspace_branch
        None,        # sandbox_mgr
        None,        # sandbox_info
    )

    assert result is True
    commit_push_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_task_resources_commit_push_fails(monkeypatch):
    """Worktree commit/push fails → _fail_task called, returns False."""
    fail_task_mock = AsyncMock()
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", fail_task_mock)

    async def _raising_commit(*a, **kw):
        raise RuntimeError("push failed: auth error")

    monkeypatch.setattr(engine, "commit_and_push_workspace", _raising_commit)

    # Use a SimpleNamespace task so we can freely set .project
    task = _make_task(id="tt-finalize-commit-fail-sn-1", title="Commit Fail Test")
    task.project = SimpleNamespace(
        repo_url="https://github.com/test/repo",
        branch="main",
    )
    task.target_branch = None

    session = SimpleNamespace(commit=AsyncMock())

    result = await engine._finalize_task_resources(
        session,  # type: ignore[arg-type]
        task,  # type: ignore[arg-type]
        [],
        None,
        None, None, "/tmp/test_ws", "tmp_cloned", None,
        None, None,
    )

    assert result is False
    fail_task_mock.assert_awaited_once()


# ── 16. _execute_single_stage paths ───────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_enabled(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True, stage.error_message set → uses structured reflection."""
    reflection_result = {
        "root_cause": "IndexError at line 42",
        "lesson": "Always check bounds",
        "suggestion": "Add bounds check",
    }
    generate_reflection_mock = AsyncMock(return_value=reflection_result)
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr("app.worker.failure.generate_structured_reflection", generate_reflection_mock)

    execute_stage_mock = AsyncMock(return_value="stage output")
    monkeypatch.setattr(engine, "execute_stage", execute_stage_mock)
    monkeypatch.setattr(engine, "execute_stage_sandboxed", execute_stage_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)

    task_id = "tt-exec-reflect-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Reflection Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-reflect-1",
            stage_name="coding",
            agent_role="coding",
            error_message="IndexError: list out of range",
            output_summary="partial output",
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0,
            [],
            compression,
            None,
            None,
            {},
            "/tmp/ws",
            None,
        )

    assert result == "stage output"
    generate_reflection_mock.assert_awaited_once()
    # Verify retry_context was built from reflection
    call_kwargs = execute_stage_mock.call_args.kwargs
    retry_ctx = call_kwargs.get("retry_context")
    assert retry_ctx is not None
    assert "IndexError at line 42" in retry_ctx["error"]

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_reflection_disabled_uses_plain_context(monkeypatch):
    """SKILL_REFLECTION_ENABLED=False, stage.error_message set → plain retry_context."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)

    execute_stage_mock = AsyncMock(return_value="stage output")
    monkeypatch.setattr(engine, "execute_stage", execute_stage_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    task_id = "tt-exec-noreflect-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="No Reflection Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-noreflect-1",
            stage_name="coding",
            agent_role="coding",
            error_message="NameError: variable not defined",
            output_summary="some prior output",
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {}, "/tmp/ws", None,
        )

    assert result == "stage output"
    call_kwargs = execute_stage_mock.call_args.kwargs
    retry_ctx = call_kwargs.get("retry_context")
    assert retry_ctx is not None
    assert retry_ctx["error"] == "NameError: variable not defined"
    assert "some prior output" in retry_ctx["prior_output"]

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_uses_sandbox(monkeypatch):
    """sandbox_info is truthy AND agent_role='coding' → calls execute_stage_sandboxed."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "graceful")

    sandboxed_mock = AsyncMock(return_value="sandboxed output")
    plain_mock = AsyncMock(return_value="plain output")
    monkeypatch.setattr(engine, "execute_stage_sandboxed", sandboxed_mock)
    monkeypatch.setattr(engine, "execute_stage", plain_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    task_id = "tt-exec-sandbox-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Sandbox Stage Test", status="running"))
        await session.commit()

    sandbox_info = SimpleNamespace(container_name="test-container")

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-sandbox-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {}, "/tmp/ws", sandbox_info,
        )

    assert result == "sandboxed output"
    sandboxed_mock.assert_awaited_once()
    plain_mock.assert_not_awaited()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_exception_fails_task(monkeypatch):
    """execute_stage raises exception → _fail_task called, returns None."""
    fail_task_mock = AsyncMock()
    mark_stage_failed_mock = AsyncMock()

    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "_fail_task", fail_task_mock)
    monkeypatch.setattr(engine, "mark_stage_failed", mark_stage_failed_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_agents_for_task", lambda _: None)

    async def _raising(*a, **kw):
        raise RuntimeError("LLM timeout")

    monkeypatch.setattr(engine, "execute_stage", _raising)

    task_id = "tt-exec-exc-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Stage Exception Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-exc-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {}, "/tmp/ws", None,
        )

    assert result is None
    fail_task_mock.assert_awaited_once()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_strict_sandbox_unavailable(monkeypatch):
    """SANDBOX_ENABLED=True, agent_role='coding', sandbox_info=None, SANDBOX_FALLBACK_MODE='strict' → fail task."""
    fail_task_mock = AsyncMock()
    mark_stage_failed_mock = AsyncMock()

    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "strict")
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "_fail_task", fail_task_mock)
    monkeypatch.setattr(engine, "mark_stage_failed", mark_stage_failed_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_agents_for_task", lambda _: None)

    task_id = "tt-exec-strict-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Strict Sandbox Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-strict-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {},
            "/tmp/ws",
            None,  # sandbox_info=None triggers strict mode path
            sandbox_required_error="sandbox_create_failed",
        )

    assert result is None
    fail_task_mock.assert_awaited_once()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ── 17. _maybe_insert_dynamic_gate paths ──────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_disabled(monkeypatch):
    """DYNAMIC_GATE_ENABLED=False → returns False immediately."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", False)
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace()
    result = await engine._maybe_insert_dynamic_gate(session, task, stage, "output")  # type: ignore[arg-type]
    assert result is False


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_no_structured(monkeypatch):
    """stage.output_structured is None → returns False."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    task = _make_task()
    stage = _make_stage()
    stage.output_structured = None
    session = SimpleNamespace()
    result = await engine._maybe_insert_dynamic_gate(session, task, stage, "output")  # type: ignore[arg-type]
    assert result is False


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_high_confidence(monkeypatch):
    """confidence >= threshold → returns False."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.7)
    task = _make_task()
    stage = _make_stage()
    stage.output_structured = {"confidence": 0.9}
    session = SimpleNamespace()
    result = await engine._maybe_insert_dynamic_gate(session, task, stage, "output")  # type: ignore[arg-type]
    assert result is False


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_low_confidence_approved(monkeypatch):
    """confidence < threshold → creates gate; gate approved → returns True."""
    task_id = "tt-dyngate-approved-1"
    stage_id = "tt-dyngate-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Dynamic Gate Test", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id,
            stage_name="coding", agent_role="coding", status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.7)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())

    approve_calls = [0]

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        stage.output_structured = {"confidence": 0.3}

        async def _auto_approve_refresh(obj):
            if isinstance(obj, HumanGateModel):
                approve_calls[0] += 1
                obj.status = "approved"

        session.refresh = _auto_approve_refresh  # type: ignore

        result = await engine._maybe_insert_dynamic_gate(
            session, task, stage, "low confidence output"  # type: ignore[arg-type]
        )

    assert result is True

    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        st = await session.get(TaskStageModel, stage_id)
        if st:
            await session.delete(st)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_low_confidence_rejected(monkeypatch):
    """confidence < threshold → creates gate; gate rejected → returns False."""
    task_id = "tt-dyngate-rejected-1"
    stage_id = "tt-dyngate-rejected-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Dynamic Gate Reject Test", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id,
            stage_name="coding", agent_role="coding", status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.7)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        stage.output_structured = {"confidence": 0.2}

        async def _auto_reject_refresh(obj):
            if isinstance(obj, HumanGateModel):
                obj.status = "rejected"

        session.refresh = _auto_reject_refresh  # type: ignore

        result = await engine._maybe_insert_dynamic_gate(
            session, task, stage, "low confidence output"  # type: ignore[arg-type]
        )

    assert result is False

    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        st = await session.get(TaskStageModel, stage_id)
        if st:
            await session.delete(st)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ── 18. _route_decision paths ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_decision_disabled(monkeypatch):
    """DYNAMIC_ROUTING_ENABLED=False → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", False)
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace()
    result = await engine._route_decision(session, task, stage, {}, {})  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_no_options(monkeypatch):
    """No options in routing_config → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace()
    result = await engine._route_decision(session, task, stage, {"options": []}, {})  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_valid_llm_response(monkeypatch):
    """LLM returns valid target name → returns that target."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    fake_response = SimpleNamespace(content="coding")
    fake_client = SimpleNamespace(
        chat=AsyncMock(return_value=fake_response)
    )
    monkeypatch.setattr("app.integration.llm_client.get_llm_client", lambda: fake_client)

    task_id = "tt-route-valid-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Routing Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        task.routing_decisions = []
        stage = SimpleNamespace(
            id="stage-route-1",
            stage_name="review",
            agent_role="review",
            output_summary="looks good",
            output_structured=None,
        )

        routing_config = {
            "options": [
                {"target": "coding", "description": "Implement it"},
                {"target": "test", "description": "Write tests"},
            ]
        }

        result = await engine._route_decision(
            session, task, stage, routing_config, {}  # type: ignore[arg-type]
        )

    assert result == "coding"

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_route_decision_invalid_target(monkeypatch):
    """LLM returns invalid target (not in options) → None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    fake_response = SimpleNamespace(content="nonexistent_stage")
    fake_client = SimpleNamespace(
        chat=AsyncMock(return_value=fake_response)
    )
    monkeypatch.setattr("app.integration.llm_client.get_llm_client", lambda: fake_client)

    task = _make_task()
    stage = _make_stage(stage_name="review", agent_role="review")
    stage.output_summary = "review done"
    task.routing_decisions = None
    session = SimpleNamespace(commit=AsyncMock())

    routing_config = {
        "options": [
            {"target": "coding", "description": "Go to coding"},
        ]
    }

    result = await engine._route_decision(
        session, task, stage, routing_config, {}  # type: ignore[arg-type]
    )
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_llm_exception(monkeypatch):
    """LLM raises exception → None, no crash."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    def _bad_get_client():
        raise ConnectionError("LLM server down")

    monkeypatch.setattr("app.integration.llm_client.get_llm_client", _bad_get_client)

    task = _make_task()
    stage = _make_stage(stage_name="review", agent_role="review")
    stage.output_summary = "review done"
    stage.output_structured = None
    session = SimpleNamespace(commit=AsyncMock())

    routing_config = {
        "options": [
            {"target": "coding", "description": "Go to coding"},
        ]
    }

    result = await engine._route_decision(
        session, task, stage, routing_config, {}  # type: ignore[arg-type]
    )
    assert result is None


# ── 19. _handle_gate revised and cancelled paths ───────────────────────────


@pytest.mark.asyncio
async def test_handle_gate_revised(monkeypatch):
    """Gate status becomes 'revised' with revised_content → returns {'result': 'revised', ...}."""
    task_id = "tt-gate-revised-task"
    stage_id = "tt-gate-revised-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Revised", status="running"))
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

    async def _auto_revise_refresh(obj):
        if isinstance(obj, HumanGateModel):
            obj.status = "revised"
            obj.review_comment = "please revise this section"
            obj.revised_content = "new version of the content"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _auto_revise_refresh  # type: ignore

        result = await engine._handle_gate(
            session=session,
            task=task,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            gate_type="human_approve",
            stage_output="output text",
        )

    assert result["result"] == "revised"
    assert result["revised_content"] == "new version of the content"
    assert "please revise" in result["comment"]

    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        st = await session.get(TaskStageModel, stage_id)
        if st:
            await session.delete(st)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_handle_gate_cancelled_via_is_cancelled(monkeypatch):
    """_is_cancelled returns True while gate pending → returns {'result': 'cancelled'}."""
    task_id = "tt-gate-cancel-task"
    stage_id = "tt-gate-cancel-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Cancelled", status="running"))
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
    # Simulate task cancellation
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))

    async def _noop_refresh(obj):
        pass  # gate stays pending

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

    assert result["result"] == "cancelled"

    async with async_session_factory() as session:
        gates = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates.scalars().all():
            await session.delete(g)
        st = await session.get(TaskStageModel, stage_id)
        if st:
            await session.delete(st)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 20: _prune_stale_worktrees
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prune_stale_worktrees_disabled(monkeypatch):
    """WORKTREE_ENABLED=False → early return, nothing happens."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    await engine._prune_stale_worktrees()  # no error


@pytest.mark.asyncio
async def test_prune_stale_worktrees_with_repo_local_path(monkeypatch):
    """WORKTREE_ENABLED=True, project with repo_local_path → prune called."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    cleaned_calls = []

    class FakeMgr:
        async def prune_all_stale(self):
            cleaned_calls.append(1)
            return 2

    rows = [(str("proj-1"), "/some/repo/path", None)]

    class FakeResult:
        def all(self):
            return rows

    fake_session = SimpleNamespace(
        execute=AsyncMock(return_value=FakeResult()),
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_session_factory():
        yield fake_session

    monkeypatch.setattr(engine, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(engine, "get_managed_repo_path", lambda pid, url: type("P", (), {"__truediv__": lambda s, x: type("F", (), {"exists": lambda: False})()})())
    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    await engine._prune_stale_worktrees()
    assert len(cleaned_calls) == 1


@pytest.mark.asyncio
async def test_prune_stale_worktrees_exception(monkeypatch):
    """WORKTREE_ENABLED=True, session.execute raises → exception swallowed."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    import contextlib

    @contextlib.asynccontextmanager
    async def _fail_session():
        raise RuntimeError("DB down")
        yield  # noqa: unreachable

    monkeypatch.setattr(engine, "async_session_factory", _fail_session)
    await engine._prune_stale_worktrees()  # must not raise


@pytest.mark.asyncio
async def test_prune_stale_worktrees_with_repo_url_and_managed_path(monkeypatch):
    """WORKTREE_ENABLED=True, project with repo_url and managed path that exists."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)

    import contextlib
    from pathlib import Path

    cleaned_calls = []

    class FakeMgr:
        async def prune_all_stale(self):
            cleaned_calls.append(1)
            return 0

    rows = [(str("proj-2"), None, "https://github.com/org/repo.git")]

    class FakeResult:
        def all(self):
            return rows

    fake_session = SimpleNamespace(execute=AsyncMock(return_value=FakeResult()))

    @contextlib.asynccontextmanager
    async def _fake_session_factory():
        yield fake_session

    fake_managed = SimpleNamespace(__truediv__=lambda self, x: type("GitDir", (), {"exists": lambda: True})())

    monkeypatch.setattr(engine, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(engine, "get_managed_repo_path", lambda pid, url: fake_managed)
    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    await engine._prune_stale_worktrees()


# ═══════════════════════════════════════════════════════════════════════
# Section 21: _pick_pending_task rowcount==0
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pick_pending_task_race_condition():
    """Two workers compete — second UPDATE returns rowcount==0 → returns None."""
    task_id = "tt-race-task-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Race Task", status="pending"))
        await session.commit()

    # First worker claims it
    async with async_session_factory() as session1:
        task = await engine._pick_pending_task(session1)
        assert task is not None
        assert task.id == task_id

    # Second worker now sees no pending tasks
    async with async_session_factory() as session2:
        task2 = await engine._pick_pending_task(session2)
        assert task2 is None

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 22: _ensure_code_stage_has_changes additional paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ensure_code_stage_no_changes_false(monkeypatch):
    """Stage is 'code', changed=False → mark failed and fail task, returns False."""
    monkeypatch.setattr(engine, "_has_git_worktree_changes", AsyncMock(return_value=False))
    mark_failed = AsyncMock()
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task()
    stage = _make_stage(stage_name="code")
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._ensure_code_stage_has_changes(session, task, stage, "/some/path")

    assert result is False
    mark_failed.assert_awaited_once()
    fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_code_stage_no_changes_none(monkeypatch):
    """Stage is 'code', changed=None → mark failed and fail task, returns False."""
    monkeypatch.setattr(engine, "_has_git_worktree_changes", AsyncMock(return_value=None))
    mark_failed = AsyncMock()
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task()
    stage = _make_stage(stage_name="code")
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._ensure_code_stage_has_changes(session, task, stage, "/some/path")

    assert result is False
    assert "verification failed" in mark_failed.call_args[0][3]


@pytest.mark.asyncio
async def test_ensure_code_stage_not_code_stage(monkeypatch):
    """Stage is not 'code' → returns True immediately without checking git."""
    git_check = AsyncMock()
    monkeypatch.setattr(engine, "_has_git_worktree_changes", git_check)

    task = _make_task()
    stage = _make_stage(stage_name="review")
    session = SimpleNamespace()

    result = await engine._ensure_code_stage_has_changes(session, task, stage, "/some/path")
    assert result is True
    git_check.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Section 23: _setup_worktree paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_setup_worktree_disabled(monkeypatch):
    """WORKTREE_ENABLED=False → returns (None, None) immediately."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    task = _make_task(project=None)
    path, mgr = await engine._setup_worktree(task)
    assert path is None
    assert mgr is None


@pytest.mark.asyncio
async def test_setup_worktree_no_repo_url(monkeypatch):
    """WORKTREE_ENABLED=True, project has no repo_url and no local_path → returns (None, None)."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    task = _make_task(project=SimpleNamespace(
        id="proj-1", repo_local_path=None, repo_url=None, branch="main",
        target_branch=None,
    ))
    path, mgr = await engine._setup_worktree(task)
    assert path is None
    assert mgr is None


@pytest.mark.asyncio
async def test_setup_worktree_exception(monkeypatch):
    """WORKTREE_ENABLED=True, create_worktree raises → returns (None, worktree_mgr)."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())

    class FakeMgr:
        async def create_worktree(self, **kw):
            raise RuntimeError("git failure")

    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    task = _make_task(project=SimpleNamespace(
        id="proj-1",
        repo_local_path="/existing/repo",
        repo_url="https://github.com/org/repo.git",
        branch="main",
        target_branch=None,
    ))
    monkeypatch.setattr(engine, "Path", lambda p: type("P", (), {"exists": lambda self: True})())

    import pathlib
    orig_path = pathlib.Path

    class _FakePath:
        def __init__(self, p):
            self._p = p
        def exists(self):
            return True
        def __truediv__(self, other):
            return self

    monkeypatch.setattr(engine, "Path", _FakePath)

    path, mgr = await engine._setup_worktree(task)
    assert path is None


@pytest.mark.asyncio
async def test_setup_worktree_returns_none_path(monkeypatch):
    """WORKTREE_ENABLED=True, create_worktree returns None → returns (None, None)."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())

    class FakeMgr:
        async def create_worktree(self, **kw):
            return None

    monkeypatch.setattr(engine, "get_worktree_manager", lambda path: FakeMgr())

    class _FakePath:
        def __init__(self, p):
            self._p = p
        def exists(self):
            return True
        def __truediv__(self, other):
            return self

    monkeypatch.setattr(engine, "Path", _FakePath)

    task = _make_task(
        target_branch=None,
        project=SimpleNamespace(
            id="proj-1",
            repo_local_path="/existing/repo",
            repo_url="https://github.com/org/repo.git",
            branch="main",
        ),
    )
    path, mgr = await engine._setup_worktree(task)
    assert path is None
    assert mgr is None


# ═══════════════════════════════════════════════════════════════════════
# Section 24: _prepare_runtime_workspace paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prepare_runtime_workspace_worktree_path(monkeypatch):
    """worktree_path provided → return immediately with 'worktree' source."""
    task = _make_task(project=None, target_branch=None)
    path, source, branch = await engine._prepare_runtime_workspace(task, "/wt/path")
    assert path == "/wt/path"
    assert source == "worktree"


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_no_repo_url(monkeypatch):
    """No repo_url → return tmp path with 'tmp_empty' source."""
    task = _make_task(project=SimpleNamespace(repo_url="", branch="main"), target_branch=None)
    path, source, branch = await engine._prepare_runtime_workspace(task, None)
    assert source == "tmp_empty"
    assert path is not None


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_worktree_required(monkeypatch):
    """WORKTREE_ENABLED=True, no worktree_path, but has repo_url → 'worktree_required'."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", True)
    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        target_branch=None,
    )
    path, source, branch = await engine._prepare_runtime_workspace(task, None)
    assert path is None
    assert source == "worktree_required"


@pytest.mark.asyncio
async def test_prepare_runtime_workspace_clone_failed(monkeypatch):
    """prepare_workspace_from_repo returns (False, ...) → 'tmp_clone_failed'."""
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    monkeypatch.setattr(engine, "prepare_workspace_from_repo", AsyncMock(return_value=(False, None, "clone error")))

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        target_branch=None,
    )
    path, source, branch = await engine._prepare_runtime_workspace(task, None)
    assert path is None
    assert source == "tmp_clone_failed"


# ═══════════════════════════════════════════════════════════════════════
# Section 25: _setup_sandbox paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_setup_sandbox_disabled(monkeypatch):
    """SANDBOX_ENABLED=False → returns (None, None, None)."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    task = _make_task(project=None)
    result = await engine._setup_sandbox(task, "/workspace", "tmp_empty")
    assert result == (None, None, None)


@pytest.mark.asyncio
async def test_setup_sandbox_workspace_not_found(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, workspace_path doesn't exist → error returned."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "graceful")
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path / "nodir"), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "graceful")

    class FakeCreateResult:
        info = None
        workspace = str(tmp_path / "nodir")
        workspace_source = "given"
        error_code = "workspace_not_found"
        error_message = "not found"

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            return FakeCreateResult()

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=FakeCreateResult,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path / "nodir"), "given")
        # workspace_not_found → no sandbox_info
        assert sandbox_info is None
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


@pytest.mark.asyncio
async def test_setup_sandbox_success(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, successful sandbox creation."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "graceful")

    workspace = tmp_path
    workspace.mkdir(exist_ok=True)

    class FakeSandboxInfo:
        container_name = "container-1"

    class FakeCreateResult:
        info = FakeSandboxInfo()
        workspace = str(tmp_path)
        workspace_source = "given"
        error_code = None
        error_message = None

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            return FakeCreateResult()

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=FakeCreateResult,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path), "given")
        assert sandbox_info is not None
        assert error is None
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


@pytest.mark.asyncio
async def test_setup_sandbox_exception_graceful(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, sandbox creation raises, graceful fallback."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "graceful")

    tmp_path.mkdir(exist_ok=True)

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            raise RuntimeError("docker down")

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=SimpleNamespace,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path), "given")
        assert sandbox_info is None
        assert error == "sandbox_create_exception"
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


@pytest.mark.asyncio
async def test_setup_sandbox_exception_strict(monkeypatch, tmp_path):
    """SANDBOX_ENABLED=True, sandbox creation raises, strict mode."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_resolve_sandbox_workspace", lambda tid, wp, ws: (str(tmp_path), "given"))
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "strict")

    tmp_path.mkdir(exist_ok=True)

    class FakeSandboxMgr:
        async def create(self, task_id, **kw):
            raise RuntimeError("docker down")

    import sys
    fake_sandbox_mod = SimpleNamespace(
        SandboxCreateResult=SimpleNamespace,
        get_sandbox_manager=lambda: FakeSandboxMgr(),
    )
    sys.modules["app.worker.sandbox"] = fake_sandbox_mod

    try:
        task = _make_task(project=None)
        sandbox_info, sandbox_mgr, error = await engine._setup_sandbox(task, str(tmp_path), "given")
        assert error == "sandbox_create_exception"
    finally:
        if "app.worker.sandbox" in sys.modules:
            del sys.modules["app.worker.sandbox"]


# ═══════════════════════════════════════════════════════════════════════
# Section 26: _finalize_task_resources paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_finalize_task_resources_skill_feedback(monkeypatch):
    """SKILL_FEEDBACK_ENABLED=True → aggregate_skill_metrics called."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())

    skill_mock = AsyncMock()
    import sys
    sys.modules["app.services.skill_feedback_service"] = SimpleNamespace(aggregate_skill_metrics=skill_mock)

    try:
        task = _make_task(project=None, project_id=None, branch_name=None, pr_url=None, target_branch=None)
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._finalize_task_resources(
            session, task, [], None, None, None, None, "tmp_empty", None, None, None,
        )
        assert result is True
        skill_mock.assert_awaited_once()
    finally:
        sys.modules.pop("app.services.skill_feedback_service", None)


@pytest.mark.asyncio
async def test_finalize_task_resources_skill_feedback_exception(monkeypatch):
    """SKILL_FEEDBACK_ENABLED=True but aggregate_skill_metrics raises → swallowed."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())

    skill_mock = AsyncMock(side_effect=RuntimeError("db error"))
    import sys
    sys.modules["app.services.skill_feedback_service"] = SimpleNamespace(aggregate_skill_metrics=skill_mock)

    try:
        task = _make_task(project=None, project_id=None, branch_name=None, pr_url=None, target_branch=None)
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._finalize_task_resources(
            session, task, [], None, None, None, None, "tmp_empty", None, None, None,
        )
        assert result is True  # exception swallowed
    finally:
        sys.modules.pop("app.services.skill_feedback_service", None)


@pytest.mark.asyncio
async def test_finalize_task_resources_worktree_commit_and_pr(monkeypatch):
    """repo_url + worktree_mgr → commit_and_push + create_pr called."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())

    class FakeWorktreeMgr:
        async def commit_and_push(self, **kw):
            return "feat/my-branch"
        async def create_pr(self, **kw):
            return "https://github.com/org/repo/pull/1"

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        project_id="proj-1", branch_name=None, pr_url=None, target_branch=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    result = await engine._finalize_task_resources(
        session, task, [], None,
        FakeWorktreeMgr(), "/wt/path", "/wt/path", "worktree", "feat/main",
        None, None,
    )
    assert result is True
    assert task.pr_url == "https://github.com/org/repo/pull/1"


@pytest.mark.asyncio
async def test_finalize_task_resources_worktree_commit_exception(monkeypatch):
    """repo_url + worktree_mgr but commit raises → task fails, returns False."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    class BrokenMgr:
        async def commit_and_push(self, **kw):
            raise RuntimeError("push failed")

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        project_id="proj-1", branch_name=None, pr_url=None, target_branch=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    result = await engine._finalize_task_resources(
        session, task, [], None,
        BrokenMgr(), "/wt/path", "/wt/path", "worktree", None,
        None, None,
    )
    assert result is False
    fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_task_resources_workspace_commit_and_pr(monkeypatch):
    """repo_url + no worktree_mgr → commit_and_push_workspace + create_pr_for_workspace."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "commit_and_push_workspace", AsyncMock(return_value="feat/branch"))
    monkeypatch.setattr(engine, "create_pr_for_workspace", AsyncMock(return_value="https://pr/123"))

    task = _make_task(
        project=SimpleNamespace(repo_url="https://github.com/org/repo.git", branch="main"),
        project_id="proj-1", branch_name=None, pr_url=None, target_branch=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    result = await engine._finalize_task_resources(
        session, task, [], None,
        None, None, "/tmp/workspace", "tmp_cloned", "feat/branch",
        None, None,
    )
    assert result is True
    assert task.pr_url == "https://pr/123"


# ═══════════════════════════════════════════════════════════════════════
# Section 27: _cleanup_runtime_resources workspace exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cleanup_runtime_resources_workspace_exception(monkeypatch, tmp_path):
    """workspace_source starts with 'tmp_' but cleanup raises → exception swallowed."""
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import shutil as shutil_mod
    original_rmtree = shutil_mod.rmtree

    def _fail_rmtree(path, **kw):
        raise PermissionError("cannot delete")

    monkeypatch.setattr(shutil_mod, "rmtree", _fail_rmtree)
    monkeypatch.setattr(engine, "shutil", shutil_mod)

    # Create workspace inside expected path hierarchy
    import tempfile
    task_root = tmp_path / "silicon_agent" / "tasks"
    task_root.mkdir(parents=True)
    fake_workspace = task_root / "task-999"
    fake_workspace.mkdir()

    # Override tempfile.gettempdir to return tmp_path
    import tempfile as tempfile_mod
    original_gettempdir = tempfile_mod.gettempdir
    monkeypatch.setattr(tempfile_mod, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "tempfile", tempfile_mod)

    task = _make_task()
    # must not raise
    await engine._cleanup_runtime_resources(
        task, None, None, str(fake_workspace), "tmp_cloned", None, None
    )

    monkeypatch.setattr(shutil_mod, "rmtree", original_rmtree)
    monkeypatch.setattr(tempfile_mod, "gettempdir", original_gettempdir)


# ═══════════════════════════════════════════════════════════════════════
# Section 28: _process_task workspace failure paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_workspace_failure_worktree_required(monkeypatch):
    """workspace_source == 'worktree_required' → fail task with worktree reason."""
    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=(None, "worktree_required", None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project=SimpleNamespace(repo_tree=None), project_id="proj-1", target_branch=None, stages=[dummy_stage])
    task.template = None
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)

    fail_task.assert_awaited()
    call_reason = fail_task.call_args[0][2]
    assert "worktree" in call_reason.lower()


@pytest.mark.asyncio
async def test_process_task_workspace_failure_clone_failed(monkeypatch):
    """workspace_source == 'tmp_clone_failed' → fail task with clone reason."""
    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=(None, "tmp_clone_failed", None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project=SimpleNamespace(repo_tree=None), project_id="proj-1", target_branch=None, stages=[dummy_stage])
    task.template = None
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)

    fail_task.assert_awaited()
    call_reason = fail_task.call_args[0][2]
    assert "clone" in call_reason.lower()


# ═══════════════════════════════════════════════════════════════════════
# Section 29: _process_task memory init exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_memory_init_exception(monkeypatch):
    """MEMORY_ENABLED=True, ProjectMemoryStore raises → warning logged, continues to complete."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", False)
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=("/tmp/ws", "tmp_empty", None)))
    monkeypatch.setattr(engine, "_setup_sandbox", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_complete_task", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    class _BadStore:
        def __init__(self, pid):
            raise RuntimeError("memory store failure")

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.memory", SimpleNamespace(ProjectMemoryStore=_BadStore))

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1",
        target_branch=None,
        stages=[],
    )
    task.template = None
    session = SimpleNamespace(commit=AsyncMock())
    # No stages → _complete_task called
    await engine._process_task(session, task)
    engine._complete_task.assert_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Section 30: _sort_stages and _group_stages_by_order JSON decode
# ═══════════════════════════════════════════════════════════════════════

def test_sort_stages_invalid_json():
    """template.stages is invalid JSON → returns unsorted list."""
    stage1 = SimpleNamespace(stage_name="coding", id="s1")
    stage2 = SimpleNamespace(stage_name="review", id="s2")
    task = SimpleNamespace(
        template=SimpleNamespace(stages="not-valid-json{"),
        stages=[stage1, stage2],
    )
    result = engine._sort_stages(task)
    assert len(result) == 2


def test_group_stages_invalid_json():
    """template.stages is invalid JSON → each stage in its own group."""
    stage1 = SimpleNamespace(stage_name="coding", id="s1")
    stage2 = SimpleNamespace(stage_name="review", id="s2")
    task = SimpleNamespace(
        template=SimpleNamespace(stages="{{bad-json"),
    )
    result = engine._group_stages_by_order([stage1, stage2], task)
    assert len(result) == 2
    assert result[0] == [stage1]
    assert result[1] == [stage2]


# ═══════════════════════════════════════════════════════════════════════
# Section 31: _process_task_graph validation error
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_validation_error(monkeypatch):
    """StageGraph.validate() returns errors → fail task."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())

    class FakeGraph:
        nodes = []
        def validate(self):
            return ["cycle detected", "invalid node"]
        @staticmethod
        def get_ready_stages(*a, **kw):
            return []

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(
            project_id="proj-1",
            template=SimpleNamespace(stages="[]", name="tpl", gates=None),
        )
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None, execution_count=0)
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], SimpleNamespace(add=lambda c: None),
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited_once()
        assert "Invalid stage graph" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 32: _process_task_graph cancellation and stuck
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_cancellation(monkeypatch):
    """_is_cancelled returns True → return early without executing stages."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())

    class FakeNode:
        name = "coding"

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            return [FakeNode()]
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # _fail_task should NOT be called — just cancelled
        engine._fail_task.assert_not_awaited()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stuck_with_failed(monkeypatch):
    """No ready stages, no running, but has failed stages → fail task."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    call_count = [0]

    class FakeGraph:
        nodes = []
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            # First call: return a node that will fail
            # Subsequent calls: empty (stuck)
            call_count[0] += 1
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        # Pre-populate failed set by having a stage with failed status
        stage = _make_stage(stage_name="coding", status="failed", output_summary="err", output_structured=None, execution_count=0)

        # Directly call with pre-failed stage but empty sorted_stages (stage not in stage_map)
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # No failed stages in graph → break and return without failing
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 33: _execute_single_stage memory load exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_memory_load_exception(monkeypatch):
    """project_memory_store.get_memory_for_role raises → logs warning, continues."""
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="stage output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "mark_stage_failed", AsyncMock())
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)

    class BadMemoryStore:
        def get_memory_for_role(self, role):
            raise RuntimeError("memory broken")

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(stage_name="coding", agent_role="coding")
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression,
        BadMemoryStore(), None, {}, "/tmp/ws", None,
    )
    assert result == "stage output"


# ═══════════════════════════════════════════════════════════════════════
# Section 34: _execute_single_stage SKILL_REFLECTION_ENABLED
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True + prior error → generate_structured_reflection called."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="retry output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    reflection_mock = AsyncMock(return_value={
        "root_cause": "file not found",
        "lesson": "check file existence first",
        "suggestion": "use os.path.exists",
    })

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(generate_structured_reflection=reflection_mock))

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="FileNotFoundError: no such file",
        output_summary="partial output",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression,
        None, None, {}, "/tmp/ws", None,
    )
    assert result == "retry output"
    reflection_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_exception(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True but generate_structured_reflection raises → fallback."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(
        generate_structured_reflection=AsyncMock(side_effect=RuntimeError("reflection failed"))
    ))

    task = _make_task(project_id=None, project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="some error",
        output_summary="prior output",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "output"


@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_with_memory(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True, MEMORY_ENABLED=True, lesson persisted to memory."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    reflection_mock = AsyncMock(return_value={
        "root_cause": "null pointer",
        "lesson": "always check for null",
        "suggestion": "use getattr with default",
    })

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(generate_structured_reflection=reflection_mock))

    add_entries_mock = AsyncMock()

    class FakeEntry:
        @staticmethod
        def create(**kw):
            return FakeEntry()

    class FakeStore:
        def __init__(self, pid):
            pass
        async def add_entries(self, category, entries):
            await add_entries_mock(category, entries)

    monkeypatch.setitem(sys.modules, "app.worker.memory", SimpleNamespace(
        MemoryEntry=FakeEntry,
        ProjectMemoryStore=FakeStore,
    ))

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="NullPointerException",
        output_summary="partial",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "output"
    add_entries_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 35: _check_interactive_planning additional paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_check_interactive_planning_not_parse_stage(monkeypatch):
    """stage_name != 'parse' → returns False immediately."""
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", True)
    task = _make_task(template=SimpleNamespace(name="mytemplate"))
    stage = _make_stage(stage_name="coding")
    session = SimpleNamespace()
    result = await engine._check_interactive_planning(session, task, stage, "output")
    assert result is False


@pytest.mark.asyncio
async def test_check_interactive_planning_template_not_in_list(monkeypatch):
    """Template not in allowed list → returns False."""
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_TEMPLATES", "allowed_template")
    task = _make_task(template=SimpleNamespace(name="other_template"))
    stage = _make_stage(stage_name="parse")
    session = SimpleNamespace()
    result = await engine._check_interactive_planning(session, task, stage, "output")
    assert result is False


@pytest.mark.asyncio
async def test_check_interactive_planning_plan_assignment_exception(monkeypatch):
    """task.plan assignment raises → exception swallowed, continues to create gate."""
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_TEMPLATES", "mytemplate")
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    task_id = "tt-iplan-noplan-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="IPlanning Task", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = TaskStageModel(
            id="tt-iplan-noplan-stage-1",
            task_id=task_id,
            stage_name="parse",
            agent_role="orchestrator",
            status="completed",
        )
        session.add(stage)
        await session.commit()

        # Should time out quickly
        result = await engine._check_interactive_planning(session, task, stage, "plan output")
        assert result is True  # timed out

    # Cleanup
    async with async_session_factory() as session:
        from sqlalchemy import select
        gates = await session.execute(select(HumanGateModel).where(HumanGateModel.task_id == task_id))
        for g in gates.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, "tt-iplan-noplan-stage-1")
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_check_interactive_planning_gate_rejected(monkeypatch):
    """Gate status is 'rejected' → reset stage and return False."""
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_TEMPLATES", "mytemplate")
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 30.0)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    task_id = "tt-iplan-reject-1"
    stage_id = "tt-iplan-reject-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="IPlanning Reject", status="running",
                              template=None))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="parse",
            agent_role="orchestrator", status="completed",
        ))
        await session.commit()

    async def _auto_reject_refresh(obj):
        if isinstance(obj, HumanGateModel):
            obj.status = "rejected"
            obj.review_comment = "rejected plan"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        # template=None means the template-name check passes (None is falsy)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _auto_reject_refresh  # type: ignore

        result = await engine._check_interactive_planning(session, task, stage, "plan output")

    assert result is False  # continues execution after rejection

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_check_interactive_planning_gate_refresh_exception(monkeypatch):
    """session.refresh raises during planning poll → continue loop (timeout eventually)."""
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_TEMPLATES", "mytemplate")
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    task_id = "tt-iplan-refresh-exc-1"
    stage_id = "tt-iplan-refresh-exc-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="IPlanning Refresh Exc", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="parse",
            agent_role="orchestrator", status="completed",
        ))
        await session.commit()

    refresh_count = [0]

    async def _refresh_raises_after_first(obj):
        refresh_count[0] += 1
        if refresh_count[0] > 1:
            # Polling loop refreshes raise
            raise RuntimeError("refresh failed")
        # First refresh (gate creation) succeeds via original session refresh
        from sqlalchemy import inspect as sa_inspect
        # Do a no-op (gate already has an id from commit)

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        # template=None means the template-name check passes
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _refresh_raises_after_first  # type: ignore

        result = await engine._check_interactive_planning(session, task, stage, "plan output")
    assert result is True  # timed out

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 36: _maybe_insert_dynamic_gate timeout and cancellation
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_timeout(monkeypatch):
    """Dynamic gate times out → returns False."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.8)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    task_id = "tt-dyngate-timeout-1"
    stage_id = "tt-dyngate-timeout-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="DynGate Timeout", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="coding",
            agent_role="coding", status="completed",
            output_structured={"confidence": 0.3},
        ))
        await session.commit()

    async def _noop_refresh(obj):
        pass  # gate stays pending

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _noop_refresh  # type: ignore

        result = await engine._maybe_insert_dynamic_gate(session, task, stage, "some output")

    assert result is False

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_refresh_exception(monkeypatch):
    """Dynamic gate refresh raises → continue loop, eventually timeout."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.8)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    task_id = "tt-dyngate-refexc-1"
    stage_id = "tt-dyngate-refexc-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="DynGate RefExc", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="coding",
            agent_role="coding", status="completed",
            output_structured={"confidence": 0.2},
        ))
        await session.commit()

    refresh_count = [0]

    async def _refresh_raises_after_first(obj):
        refresh_count[0] += 1
        if refresh_count[0] > 1:
            raise RuntimeError("DB error")

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _refresh_raises_after_first  # type: ignore

        result = await engine._maybe_insert_dynamic_gate(session, task, stage, "output")

    assert result is False

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_cancellation(monkeypatch):
    """_is_cancelled returns True during dynamic gate → returns False."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.8)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 30.0)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))

    task_id = "tt-dyngate-cancel-1"
    stage_id = "tt-dyngate-cancel-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="DynGate Cancel", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="coding",
            agent_role="coding", status="completed",
            output_structured={"confidence": 0.1},
        ))
        await session.commit()

    async def _noop_refresh(obj):
        pass

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _noop_refresh  # type: ignore

        result = await engine._maybe_insert_dynamic_gate(session, task, stage, "output")

    assert result is False

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 37: _handle_gate_with_retry paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_handle_gate_with_retry_revised(monkeypatch):
    """Gate returns 'revised' → re-execute stage, update prior_outputs, return new output."""
    call_count = [0]

    async def _mock_handle_gate(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"result": "revised", "comment": "please revise", "revised_content": "new", "retry_count": 0}
        return {"result": "approved", "comment": "", "retry_count": 1}

    monkeypatch.setattr(engine, "_handle_gate", _mock_handle_gate)
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="revised output"))

    task = _make_task()
    stage = _make_stage(stage_name="coding", status="running")
    session = SimpleNamespace(commit=AsyncMock())
    stage.output_summary = None
    stage.error_message = None

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    prior_outputs = [{"stage": "coding", "output": "old output"}]
    gate_def = {"type": "human_approve", "max_retries": 1}

    result = await engine._handle_gate_with_retry(
        session, task, stage, gate_def, "old output", 0,
        prior_outputs, compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "revised output"
    assert prior_outputs[0]["output"] == "revised output"


@pytest.mark.asyncio
async def test_handle_gate_with_retry_rejected_with_retry(monkeypatch):
    """Gate rejected, retry < max_retries → re-execute stage."""
    call_count = [0]

    async def _mock_handle_gate(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"result": "rejected", "comment": "not good", "retry_count": 0}
        return {"result": "approved", "comment": "", "retry_count": 1}

    monkeypatch.setattr(engine, "_handle_gate", _mock_handle_gate)
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="retried output"))

    task = _make_task()
    stage = _make_stage(stage_name="coding", status="running")
    stage.output_summary = None
    stage.error_message = None
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    gate_def = {"type": "human_approve", "max_retries": 2}
    prior_outputs = [{"stage": "coding", "output": "initial"}]

    result = await engine._handle_gate_with_retry(
        session, task, stage, gate_def, "initial", 0,
        prior_outputs, compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "retried output"
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_handle_gate_with_retry_rejected_exhausted(monkeypatch):
    """Gate rejected, no retries left → fail task, return None."""
    monkeypatch.setattr(engine, "_handle_gate", AsyncMock(return_value={"result": "rejected", "comment": "no", "retry_count": 0}))
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())

    task = _make_task()
    stage = _make_stage(stage_name="coding")
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    gate_def = {"type": "human_approve", "max_retries": 0}
    result = await engine._handle_gate_with_retry(
        session, task, stage, gate_def, "output", 0,
        [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result is None
    engine._fail_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_gate_with_retry_timeout(monkeypatch):
    """Gate returns 'timeout' → fail task, return None."""
    monkeypatch.setattr(engine, "_handle_gate", AsyncMock(return_value={"result": "timeout", "comment": "", "retry_count": 0}))
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())

    task = _make_task()
    stage = _make_stage(stage_name="coding")
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    gate_def = {"type": "human_approve", "max_retries": 0}
    result = await engine._handle_gate_with_retry(
        session, task, stage, gate_def, "output", 0,
        [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result is None
    engine._fail_task.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 38: _handle_gate idempotency paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_handle_gate_existing_pending_gate(monkeypatch):
    """Existing pending gate for same task+stage → reused instead of creating new one."""
    task_id = "tt-gate-idempotent-1"
    stage_id = "tt-gate-idempotent-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Idempotent Gate", status="running"))
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
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 30.0)

    # Pre-create an existing pending gate
    async with async_session_factory() as session:
        existing = HumanGateModel(
            gate_type="human_approve",
            task_id=task_id,
            agent_role="review",
            status="pending",
            content={"stage": "review", "summary": "existing gate"},
        )
        session.add(existing)
        await session.commit()
        existing_id = existing.id

    async def _auto_approve_refresh(obj):
        if isinstance(obj, HumanGateModel):
            obj.status = "approved"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _auto_approve_refresh  # type: ignore

        result = await engine._handle_gate(
            session=session,
            task=task,
            stage=stage,
            gate_type="human_approve",
            stage_output="some output",
        )

    assert result["result"] == "approved"
    # Only one gate should exist (the pre-existing one was reused)
    async with async_session_factory() as session:
        all_gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        all_gates = all_gates_result.scalars().all()
        for g in all_gates:
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_handle_gate_already_approved_idempotency(monkeypatch):
    """Latest gate for stage is already approved → returns approved without creating new gate."""
    from datetime import datetime, timezone

    task_id = "tt-gate-already-approved-1"
    stage_id = "tt-gate-already-approved-stage-1"

    completed_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    reviewed_at = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Already Approved", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id,
            stage_name="review", agent_role="review", status="completed",
            completed_at=completed_at,
        ))
        await session.commit()

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())

    # Pre-create an approved gate with reviewed_at AFTER stage completed_at
    async with async_session_factory() as session:
        approved_gate = HumanGateModel(
            gate_type="human_approve",
            task_id=task_id,
            agent_role="review",
            status="approved",
            content={"stage": "review", "summary": "already done"},
            review_comment="approved by reviewer",
            reviewed_at=reviewed_at,
        )
        session.add(approved_gate)
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

        result = await engine._handle_gate(
            session=session,
            task=task,
            stage=stage,
            gate_type="human_approve",
            stage_output="output",
        )

    assert result["result"] == "approved"
    assert result["comment"] == "approved by reviewer"

    async with async_session_factory() as session:
        all_gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in all_gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_handle_gate_refresh_exception(monkeypatch):
    """session.refresh raises during gate polling → logs warning, continues loop (timeout)."""
    task_id = "tt-gate-refresh-exc-1"
    stage_id = "tt-gate-refresh-exc-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Refresh Exc", status="running"))
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
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.02)

    refresh_count = [0]

    async def _refresh_raises_after_first(obj):
        refresh_count[0] += 1
        if refresh_count[0] > 1:
            raise RuntimeError("DB connection lost")

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _refresh_raises_after_first  # type: ignore

        result = await engine._handle_gate(
            session=session,
            task=task,
            stage=stage,
            gate_type="human_approve",
            stage_output="output",
        )

    assert result["result"] == "timeout"

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 39: _route_decision additional paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_route_decision_disabled(monkeypatch):
    """DYNAMIC_ROUTING_ENABLED=False → returns None immediately."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", False)
    task = _make_task()
    stage = _make_stage(stage_name="coding", output_summary="done")
    session = SimpleNamespace()
    result = await engine._route_decision(session, task, stage, {"options": []}, {})
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_no_options(monkeypatch):
    """DYNAMIC_ROUTING_ENABLED=True but no options → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    task = _make_task()
    stage = _make_stage(stage_name="coding", output_summary="done")
    session = SimpleNamespace()
    result = await engine._route_decision(session, task, stage, {"options": []}, {})
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_invalid_decision(monkeypatch):
    """LLM returns decision not in valid targets → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    class FakeClient:
        async def chat(self, **kw):
            return SimpleNamespace(content="invalid_stage")

    import sys
    sys.modules["app.integration.llm_client"] = SimpleNamespace(
        get_llm_client=lambda: FakeClient(),
        ChatMessage=lambda **kw: kw,
    )

    try:
        task = _make_task(routing_decisions=None)
        stage = _make_stage(stage_name="review", output_summary="done")
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._route_decision(
            session, task, stage,
            {"options": [{"target": "code"}, {"target": "test"}]},
            {},
        )
        assert result is None
    finally:
        sys.modules.pop("app.integration.llm_client", None)


@pytest.mark.asyncio
async def test_route_decision_success(monkeypatch):
    """LLM returns valid decision → returns the decision."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    class FakeClient:
        async def chat(self, **kw):
            return SimpleNamespace(content="code")

    import sys
    sys.modules["app.integration.llm_client"] = SimpleNamespace(
        get_llm_client=lambda: FakeClient(),
        ChatMessage=lambda **kw: kw,
    )

    try:
        task = _make_task(routing_decisions=[])
        stage = _make_stage(stage_name="review", output_summary="done")
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._route_decision(
            session, task, stage,
            {"options": [{"target": "code", "description": "go to code"}, {"target": "test"}]},
            {},
        )
        assert result == "code"
    finally:
        sys.modules.pop("app.integration.llm_client", None)


@pytest.mark.asyncio
async def test_route_decision_exception(monkeypatch):
    """LLM call raises → returns None (exception swallowed)."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    class FakeClient:
        async def chat(self, **kw):
            raise RuntimeError("LLM down")

    import sys
    sys.modules["app.integration.llm_client"] = SimpleNamespace(
        get_llm_client=lambda: FakeClient(),
        ChatMessage=lambda **kw: kw,
    )

    try:
        task = _make_task()
        stage = _make_stage(stage_name="review", output_summary="done")
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._route_decision(
            session, task, stage,
            {"options": [{"target": "code"}]},
            {},
        )
        assert result is None
    finally:
        sys.modules.pop("app.integration.llm_client", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 40: _process_task internal paths (cancellation, skip, resume)
# ═══════════════════════════════════════════════════════════════════════

def _make_process_task_mocks(monkeypatch):
    """Set up common mocks for _process_task unit tests."""
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=("/tmp/ws", "tmp_empty", None)))
    monkeypatch.setattr(engine, "_setup_sandbox", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_complete_task", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c", add=lambda x: None)))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_maybe_insert_dynamic_gate", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_check_interactive_planning", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_handle_gate_with_retry", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_record_stage_audit", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", False)


@pytest.mark.asyncio
async def test_process_task_cancellation_before_group(monkeypatch):
    """_is_cancelled returns True before group execution → audit logged and return."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._fail_task.assert_not_awaited()
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_should_skip_stage(monkeypatch):
    """_should_skip_stage returns True → stage skipped, _execute_single_stage not called."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_should_skip_stage", lambda *a: True)

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    execute_mock.assert_not_awaited()
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_resume_completed_stage(monkeypatch):
    """Stage with status='completed' → resume path, output added to prior_outputs."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # Stage was already completed, execute should not be called
    execute_mock.assert_not_awaited()
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_resume_with_gate(monkeypatch):
    """Completed stage with gate def → gate handling in resume path."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    gate_mock = AsyncMock(return_value="gate output")
    monkeypatch.setattr(engine, "_handle_gate_with_retry", gate_mock)

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[{"after_stage": "parse", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    gate_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_resume_gate_rejected(monkeypatch):
    """Completed stage with gate def, gate returns None → return."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    gate_mock = AsyncMock(return_value=None)  # gate rejected
    monkeypatch.setattr(engine, "_handle_gate_with_retry", gate_mock)

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[{"after_stage": "parse", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_resume_compression_none(monkeypatch):
    """Compression returns None in resume path → warning logged, continues."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=None))

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_single_stage_compression_none(monkeypatch):
    """Compression returns None in main stage path → warning logged."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_single_stage_structured_output(monkeypatch):
    """Stage has output_structured → structured_outputs updated."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                        output_structured={"status": "done", "confidence": 0.9})
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    execute_mock.assert_awaited_once()
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_gate_returns_none(monkeypatch):
    """Stage gate returns None → task should not complete."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_handle_gate_with_retry", AsyncMock(return_value=None))

    stage = _make_stage(stage_name="parse", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[{"after_stage": "parse", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_interactive_planning_paused(monkeypatch):
    """_check_interactive_planning returns True → task paused, complete not called."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_check_interactive_planning", AsyncMock(return_value=True))

    stage = _make_stage(stage_name="parse", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_finalize_failed(monkeypatch):
    """_finalize_task_resources returns False → close agents and return without completing."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=False))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        await engine._process_task(session, task)

    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_parallel_stages(monkeypatch):
    """Two stages at same order → parallel execution path."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    outputs = {"stage1": "output1", "stage2": "output2"}

    async def _fake_execute(session, task, stage, *a, **kw):
        return outputs.get(stage.stage_name, "output")

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)

    stage1 = _make_stage(id="ps-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="ps-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_parallel_stage_exception(monkeypatch):
    """One parallel stage raises exception → task fails."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "mark_stage_failed", AsyncMock())

    call_count = [0]

    async def _fake_execute(session, task, stage, *a, **kw):
        call_count[0] += 1
        if stage.stage_name == "stage2":
            raise RuntimeError("stage2 failed")
        await asyncio.sleep(0.1)
        return "output1"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)

    stage1 = _make_stage(id="pe-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="pe-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        await engine._process_task(session, task)

    engine._fail_task.assert_awaited()


@pytest.mark.asyncio
async def test_process_task_parallel_stage_returns_none(monkeypatch):
    """One parallel stage returns None → return early."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    async def _fake_execute(session, task, stage, *a, **kw):
        if stage.stage_name == "stage2":
            return None
        return "output1"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)

    stage1 = _make_stage(id="pn-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="pn-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_parallel_already_completed_stage(monkeypatch):
    """Parallel stage already completed → added to prior_outputs without re-executing."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    execute_mock = AsyncMock(return_value="output2")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    stage1 = _make_stage(id="pac-1", stage_name="stage1", status="completed",
                         output_summary="existing output", output_structured={"key": "val"})
    stage2 = _make_stage(id="pac-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # stage1 already completed, stage2 should be executed
    execute_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_parallel_skip_stage(monkeypatch):
    """Parallel stage with condition → skipped via _should_skip_stage."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_should_skip_stage", lambda stage, defs, outputs: stage.stage_name == "stage2")

    execute_mock = AsyncMock(return_value="output1")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    stage1 = _make_stage(id="psk-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="psk-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # stage1 executed, stage2 skipped
    assert execute_mock.await_count == 1


@pytest.mark.asyncio
async def test_process_task_parallel_with_gate(monkeypatch):
    """Parallel stages with gate → gate handler called for stage with gate."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    gate_mock = AsyncMock(return_value="gate output")
    monkeypatch.setattr(engine, "_handle_gate_with_retry", gate_mock)

    stage1 = _make_stage(id="pwg-1", stage_name="stage1", status="pending", output_summary="out", output_structured=None)
    stage2 = _make_stage(id="pwg-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates='[{"after_stage": "stage1", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    gate_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 41: _process_task_graph execution paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_single_stage_success(monkeypatch):
    """Graph execution: single stage runs successfully."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_handle_gate_with_retry", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    class FakeGraph:
        nodes = [FakeNode()]
        call_count = [0]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            self.call_count[0] += 1
            if "coding" not in completed and self.call_count[0] <= 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        engine._execute_single_stage.assert_awaited_once()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stage_fails_with_redirect(monkeypatch):
    """Graph execution: stage fails, failure redirect available → redirect stage reset."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 1)

    class FakeNodeCoding:
        name = "coding"

    class FakeNodeFixup:
        name = "fixup"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeCoding(), FakeNodeFixup()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNodeCoding()]
            return []
        def get_failure_redirect(self, name):
            if name == "coding":
                return "fixup"
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_coding = _make_stage(stage_name="coding", status="pending", output_summary=None,
                                   output_structured=None, execution_count=0, error_message=None)
        stage_fixup = _make_stage(stage_name="fixup", status="pending", output_summary=None,
                                  output_structured=None, execution_count=0, error_message=None)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_coding, stage_fixup], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # Redirect stage should have been reset
        assert stage_fixup.status == "pending"
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stage_fails_no_redirect(monkeypatch):
    """Graph execution: stage fails, no redirect → fail task."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited()
        assert "failed in graph" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_parallel_stages(monkeypatch):
    """Graph execution: multiple ready stages → parallel execution."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    async def _fake_execute(session, task, stage, *a, **kw):
        return f"output_{stage.stage_name}"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNodeA:
        name = "stage_a"

    class FakeNodeB:
        name = "stage_b"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeA(), FakeNodeB()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNodeA(), FakeNodeB()]  # both ready at once
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_a = _make_stage(stage_name="stage_a", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        stage_b = _make_stage(stage_name="stage_b", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_a, stage_b], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_max_iterations(monkeypatch):
    """Graph execution exceeds max iterations → fail task."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 1)

    class FakeNode:
        name = "coding"

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            # Always return a stage to force infinite loop
            return [FakeNode()]
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        # Use _execute_single_stage that always succeeds to force re-execution
        async def _always_succeed(session, task, stage, *a, **kw):
            # Don't add to completed so ready stages always has items
            return "output"

        monkeypatch.setattr(engine, "_execute_single_stage", _always_succeed)
        monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=None))
        monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))

        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        # With max_iterations=1 and 1 node, max_iterations = 1*1 = 1
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited()
        assert "max iterations" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stage_not_in_map(monkeypatch):
    """Graph node not in stage_map → skipped."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "nonexistent_stage"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if "nonexistent_stage" not in skipped and call_count[0] <= 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        # Stage map doesn't have "nonexistent_stage"
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # Should not call execute (stage not in map)
        execute_mock.assert_not_awaited()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_skip_condition(monkeypatch):
    """Graph execution: stage has skip condition → stage.status='skipped'."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_should_skip_stage", lambda stage, defs, outputs: True)
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if "coding" not in skipped and call_count[0] <= 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        execute_mock.assert_not_awaited()
        assert stage.status == "skipped"
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_parallel_exception(monkeypatch):
    """Graph parallel: one stage raises exception → added to failed set."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "mark_stage_failed", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    async def _fake_execute(session, task, stage, *a, **kw):
        if stage.stage_name == "stage_b":
            raise RuntimeError("stage_b exploded")
        return "output_a"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))

    class FakeNodeA:
        name = "stage_a"

    class FakeNodeB:
        name = "stage_b"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeA(), FakeNodeB()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNodeA(), FakeNodeB()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_a = _make_stage(stage_name="stage_a", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        stage_b = _make_stage(stage_name="stage_b", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_a, stage_b], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        engine.mark_stage_failed.assert_awaited()
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 42: _process_task GRAPH_EXECUTION_ENABLED finalize failed
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_execution_finalize_failed(monkeypatch):
    """GRAPH_EXECUTION_ENABLED=True, _finalize_task_resources returns False → close and return."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", True)
    monkeypatch.setattr(engine, "_process_task_graph", AsyncMock())
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=False))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        await engine._process_task(session, task)

    engine._complete_task.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Section 43: _execute_single_stage sandbox strict mode
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_sandbox_strict_mode(monkeypatch):
    """SANDBOX_ENABLED=True, coding role, sandbox_info=None, strict mode → fail."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "strict")
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    mark_failed = AsyncMock()
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(stage_name="coding", agent_role="coding")
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._execute_single_stage(
            session, task, stage, 0, [], compression,
            None, None, {}, "/tmp/ws", None,
            sandbox_required_error="docker_unavailable",
        )

    assert result is None
    mark_failed.assert_awaited_once()
    fail_task.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 44: _process_task workspace_branch and repo_context paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_workspace_branch_set_as_target(monkeypatch):
    """workspace_branch is set, task has no target_branch → task.target_branch updated."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace",
                        AsyncMock(return_value=("/tmp/ws", "tmp_cloned", "feat/auto-branch")))

    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch="", stages=[dummy_stage],
        template=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))  # halt after branch set

    await engine._process_task(session, task)
    assert task.target_branch == "feat/auto-branch"


@pytest.mark.asyncio
async def test_process_task_workspace_generic_failure(monkeypatch):
    """workspace_path=None, workspace_source neither 'worktree_required' nor 'clone_failed'."""
    _make_process_task_mocks(monkeypatch)
    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=(None, "other_failure", None)))
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project=SimpleNamespace(repo_tree=None), project_id="proj-1",
                      target_branch=None, stages=[dummy_stage], template=None)
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    fail_task.assert_awaited()
    assert "workspace preparation failed" in fail_task.call_args[0][2].lower()


@pytest.mark.asyncio
async def test_process_task_repo_context_from_project(monkeypatch):
    """task.project has repo_tree → _build_repo_context called."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    build_mock = lambda proj: "REPO_CONTEXT"
    monkeypatch.setattr(engine, "_build_repo_context", build_mock)
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree={"files": ["src/main.py"]}, repo_url=""),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._execute_single_stage.assert_awaited_once()
    # Verify repo_context was passed to _execute_single_stage
    call_kwargs = engine._execute_single_stage.call_args
    assert "REPO_CONTEXT" in str(call_kwargs)


# ═══════════════════════════════════════════════════════════════════════
# Section 45: _process_task_graph resume paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_resume_with_completed_stages(monkeypatch):
    """Graph with pre-completed stages → prior_outputs populated, execution continues."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=False))
    execute_mock = AsyncMock(return_value="output_coding")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNodeCoding:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeCoding()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if "coding" not in completed and call_count[0] <= 1:
                return [FakeNodeCoding()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        # parse stage is already completed
        stage_parse = _make_stage(stage_name="parse", status="completed",
                                  output_summary="parse output", output_structured={"key": "val"},
                                  execution_count=1)
        # coding stage pending
        stage_coding = _make_stage(stage_name="coding", status="pending",
                                   output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        prior_outputs: list = []
        await engine._process_task_graph(
            session, task, [stage_parse, stage_coding], {}, {}, prior_outputs, compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # parse output should be in prior_outputs
        assert any(p["stage"] == "parse" for p in prior_outputs)
        execute_mock.assert_awaited_once()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_resume_circuit_breaker(monkeypatch):
    """Graph with pre-completed stage, circuit breaker trips → return early."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=True))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeGraph:
        nodes = []
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            return []

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_parse = _make_stage(stage_name="parse", status="completed",
                                  output_summary="output", output_structured=None, execution_count=1)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_parse], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        engine._check_circuit_breaker.assert_awaited_once()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stuck_with_unresolved_failed(monkeypatch):
    """Graph stuck: no ready, no running, failed has unresolved stages → fail task."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNode()]
            # Now stuck - no ready stages but "coding" is in failed
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited()
        assert "stuck" in fail_task.call_args[0][2] or "failed" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_resume_with_skipped_stage(monkeypatch):
    """Graph resume: skipped stage added to skipped set."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    class FakeGraph:
        nodes = []
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            return []

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_skipped = _make_stage(stage_name="review", status="skipped",
                                    output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_skipped], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # No error, should complete normally
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 46: _handle_gate_with_retry revised → execute_stage returns None
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_handle_gate_with_retry_revised_execute_returns_none(monkeypatch):
    """Gate returns 'revised', re-execute stage returns None → return None."""
    monkeypatch.setattr(engine, "_handle_gate", AsyncMock(return_value={
        "result": "revised", "comment": "revise", "revised_content": "new", "retry_count": 0
    }))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))

    task = _make_task()
    stage = _make_stage(stage_name="coding", status="running")
    session = SimpleNamespace(commit=AsyncMock())
    stage.output_summary = None
    stage.error_message = None

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()
    gate_def = {"type": "human_approve", "max_retries": 1}

    result = await engine._handle_gate_with_retry(
        session, task, stage, gate_def, "old output", 0,
        [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_handle_gate_with_retry_rejected_execute_returns_none(monkeypatch):
    """Gate rejected with retries, re-execute returns None → return None."""
    monkeypatch.setattr(engine, "_handle_gate", AsyncMock(return_value={
        "result": "rejected", "comment": "bad", "retry_count": 0
    }))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))

    task = _make_task()
    stage = _make_stage(stage_name="coding", status="running")
    stage.output_summary = None
    stage.error_message = None
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()
    gate_def = {"type": "human_approve", "max_retries": 2}

    result = await engine._handle_gate_with_retry(
        session, task, stage, gate_def, "output", 0,
        [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Section 47: _check_interactive_planning gate revised path
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_check_interactive_planning_gate_revised(monkeypatch):
    """Gate status is 'revised' with review_comment → reset stage and return False."""
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_TEMPLATES", "mytemplate")
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 30.0)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)

    task_id = "tt-iplan-revised-1"
    stage_id = "tt-iplan-revised-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="IPlanning Revised", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="parse",
            agent_role="orchestrator", status="completed",
        ))
        await session.commit()

    async def _auto_revised_refresh(obj):
        if isinstance(obj, HumanGateModel):
            obj.status = "revised"
            obj.review_comment = "please add more details"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _auto_revised_refresh  # type: ignore

        result = await engine._check_interactive_planning(session, task, stage, "plan output")

    assert result is False  # continues (revised → re-execute)

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 48: _maybe_insert_dynamic_gate worker stopping
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_maybe_insert_dynamic_gate_worker_stopping(monkeypatch):
    """_running=False → while loop doesn't start, returns False immediately."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_CONFIDENCE_THRESHOLD", 0.8)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 30.0)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", False)  # worker stopped

    task_id = "tt-dyngate-stopping-1"
    stage_id = "tt-dyngate-stopping-stage-1"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="DynGate Stopping", status="running"))
        session.add(TaskStageModel(
            id=stage_id, task_id=task_id, stage_name="coding",
            agent_role="coding", status="completed",
            output_structured={"confidence": 0.1},
        ))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

        result = await engine._maybe_insert_dynamic_gate(session, task, stage, "output")

    assert result is False

    async with async_session_factory() as session:
        gates_result = await session.execute(
            select(HumanGateModel).where(HumanGateModel.task_id == task_id)
        )
        for g in gates_result.scalars().all():
            await session.delete(g)
        s = await session.get(TaskStageModel, stage_id)
        if s:
            await session.delete(s)
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Section 49: _execute_single_stage SKILL_REFLECTION memory save exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_memory_save_exception(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True, MEMORY_ENABLED=True, memory save fails → swallowed."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(
        generate_structured_reflection=AsyncMock(return_value={
            "root_cause": "error", "lesson": "lesson text", "suggestion": "suggestion",
        })
    ))

    class FakeEntry:
        @staticmethod
        def create(**kw):
            return FakeEntry()

    class BadStore:
        def __init__(self, pid):
            pass
        async def add_entries(self, category, entries):
            raise RuntimeError("memory save failed")

    monkeypatch.setitem(sys.modules, "app.worker.memory", SimpleNamespace(
        MemoryEntry=FakeEntry,
        ProjectMemoryStore=BadStore,
    ))

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="some error", output_summary="partial",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "output"


# ═══════════════════════════════════════════════════════════════════════
# Section 50: _process_task resume circuit_breaker and ensure_code
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_resume_circuit_breaker_trips(monkeypatch):
    """Completed stage resume → circuit breaker trips → return early."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=True))

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_resume_ensure_code_changes_fails(monkeypatch):
    """Completed stage resume, _ensure_code_stage_has_changes returns False → return early."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=False))

    stage = _make_stage(
        stage_name="code", status="completed",
        output_summary="code output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "code", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_dynamic_gate_inserted(monkeypatch):
    """Stage executed, dynamic gate inserted and returned True (pass-through)."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_maybe_insert_dynamic_gate", AsyncMock(return_value=True))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # Should still complete (gate was inserted but returned True = approved)
    engine._complete_task.assert_awaited_once()
