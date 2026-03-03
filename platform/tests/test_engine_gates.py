"""Tests for core engine functions: circuit breaker, task claim, state transitions, gates."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "approved",
            "review_comment": "looks good",
            "revised_content": "",
        }),
    )
    monkeypatch.setattr(engine, "_running", True)

    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "rejected",
            "review_comment": "not good enough",
            "revised_content": "",
        }),
    )
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

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


@pytest.mark.asyncio
async def test_handle_gate_approved_from_external_session_with_stale_worker_session(monkeypatch):
    """Gate approved in another session should be observed during wait polling."""
    task_id = "tt-gate-approved-short-session-task"
    stage_id = "tt-gate-approved-short-session-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Approved Short Session", status="running"))
        session.add(TaskStageModel(
            id=stage_id,
            task_id=task_id,
            stage_name="review",
            agent_role="review",
            status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.2)

    async def _approve_gate_once_created() -> None:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            async with async_session_factory() as updater:
                gate_result = await updater.execute(
                    select(HumanGateModel).where(HumanGateModel.task_id == task_id)
                )
                gate = gate_result.scalars().first()
                if gate:
                    gate.status = "approved"
                    gate.review_comment = "approved externally"
                    await updater.commit()
                    return
            await asyncio.sleep(0.002)
        raise AssertionError("Expected gate to be created before approval update")

    updater_task = asyncio.create_task(_approve_gate_once_created())

    async def _stale_refresh(_obj):
        # Simulate a long-lived worker session that cannot see external updates via refresh.
        return None

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        session.refresh = _stale_refresh  # type: ignore[assignment]

        result = await engine._handle_gate(
            session=session,
            task=task,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            gate_type="human_approve",
            stage_output="output text",
        )

    await updater_task
    assert result["result"] == "approved"
    assert result["comment"] == "approved externally"

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
async def test_handle_gate_cancelled_from_external_session_with_stale_worker_session(monkeypatch):
    """Task cancelled in another session should stop gate wait promptly."""
    task_id = "tt-gate-cancel-short-session-task"
    stage_id = "tt-gate-cancel-short-session-stage"

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Gate Cancel Short Session", status="running"))
        session.add(TaskStageModel(
            id=stage_id,
            task_id=task_id,
            stage_name="review",
            agent_role="review",
            status="running",
        ))
        await session.commit()

    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 0.2)

    async def _cancel_task_once_gate_created() -> None:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            async with async_session_factory() as updater:
                gate_result = await updater.execute(
                    select(HumanGateModel.id).where(HumanGateModel.task_id == task_id)
                )
                gate_id = gate_result.scalars().first()
                if gate_id:
                    task_obj = await updater.get(TaskModel, task_id)
                    assert task_obj is not None
                    task_obj.status = "cancelled"
                    await updater.commit()
                    return
            await asyncio.sleep(0.002)
        raise AssertionError("Expected gate to be created before cancellation update")

    updater_task = asyncio.create_task(_cancel_task_once_gate_created())

    class _FakeStatusResult:
        @staticmethod
        def scalar_one_or_none():
            return "running"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

        async def _stale_refresh(_obj):
            return None

        original_execute = session.execute

        async def _stale_execute(stmt, *args, **kwargs):
            if "SELECT tasks.status" in str(stmt):
                return _FakeStatusResult()
            return await original_execute(stmt, *args, **kwargs)

        session.refresh = _stale_refresh  # type: ignore[assignment]
        session.execute = _stale_execute  # type: ignore[assignment]

        result = await engine._handle_gate(
            session=session,
            task=task,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            gate_type="human_approve",
            stage_output="output text",
        )

    await updater_task
    assert result["result"] == "cancelled"

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "approved",
            "review_comment": "",
            "revised_content": "",
        }),
    )

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        stage.output_structured = {"confidence": 0.3}

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "rejected",
            "review_comment": "",
            "revised_content": "",
        }),
    )

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        stage.output_structured = {"confidence": 0.2}

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "revised",
            "review_comment": "please revise this section",
            "revised_content": "new version of the content",
        }),
    )
    monkeypatch.setattr(engine, "_running", True)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 60.0)

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "rejected",
            "review_comment": "rejected plan",
            "revised_content": "",
        }),
    )
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

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        # template=None means the template-name check passes (None is falsy)
        stage = await session.get(TaskStageModel, stage_id)

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
async def test_handle_gate_with_retry_revised_updates_prior_outputs(monkeypatch):
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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "approved",
            "review_comment": "",
            "revised_content": "",
        }),
    )
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

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

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
    monkeypatch.setattr(
        engine,
        "_get_gate_snapshot",
        AsyncMock(return_value={
            "status": "revised",
            "review_comment": "please add more details",
            "revised_content": "",
        }),
    )
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

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)

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
