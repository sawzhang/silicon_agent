from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

import pytest

from app.db.session import async_session_factory
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.worker import engine


@pytest.mark.asyncio
async def test_compress_with_log_emits_started_and_success(monkeypatch):
    task = SimpleNamespace(id='task-1')
    stage = SimpleNamespace(id='stage-1', stage_name='coding', agent_role='coding')
    emitted: list[dict] = []

    async def _capture_emit(_task, **kwargs):
        emitted.append(kwargs)
        return f"log-{len(emitted)}"

    monkeypatch.setattr(engine, '_emit_system_log', AsyncMock(side_effect=_capture_emit))
    close_started = AsyncMock()
    monkeypatch.setattr(engine, '_close_started_system_log', close_started)
    monkeypatch.setattr(
        engine,
        'compress_stage_output',
        AsyncMock(return_value=SimpleNamespace(l0='a', l1='b', l2='c')),
    )

    result = await engine._compress_with_log(task, stage, 'hello world')
    assert result is not None
    assert result.l0 == 'a'
    assert [item['event_type'] for item in emitted] == [
        'compression_started',
        'compression_finished',
    ]
    assert emitted[0]['status'] == 'running'
    assert emitted[1]['status'] == 'success'
    close_started.assert_awaited_once()
    assert close_started.await_args.kwargs['started_log_id'] == 'log-1'
    assert close_started.await_args.kwargs['status'] == 'success'


@pytest.mark.asyncio
async def test_compress_with_log_emits_failed_on_exception(monkeypatch):
    task = SimpleNamespace(id='task-2')
    stage = SimpleNamespace(id='stage-2', stage_name='coding', agent_role='coding')
    emitted: list[dict] = []

    async def _capture_emit(_task, **kwargs):
        emitted.append(kwargs)
        return f"log-{len(emitted)}"

    monkeypatch.setattr(engine, '_emit_system_log', AsyncMock(side_effect=_capture_emit))
    close_started = AsyncMock()
    monkeypatch.setattr(engine, '_close_started_system_log', close_started)
    monkeypatch.setattr(
        engine,
        'compress_stage_output',
        AsyncMock(side_effect=RuntimeError('compression exploded')),
    )

    result = await engine._compress_with_log(task, stage, 'hello world')
    assert result is None
    assert [item['event_type'] for item in emitted] == [
        'compression_started',
        'compression_finished',
    ]
    assert emitted[1]['status'] == 'failed'
    assert emitted[1]['response_body']['error'] == 'compression exploded'
    close_started.assert_awaited_once()
    assert close_started.await_args.kwargs['started_log_id'] == 'log-1'
    assert close_started.await_args.kwargs['status'] == 'failed'


@pytest.mark.asyncio
async def test_handle_gate_emits_started_and_cancelled_when_worker_stops(monkeypatch):
    task_id = 'tt-engine-gate-task'
    stage_id = 'tt-engine-gate-stage'

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title='Gate Test', status='running'))
        session.add(
            TaskStageModel(
                id=stage_id,
                task_id=task_id,
                stage_name='review',
                agent_role='review',
                status='running',
            )
        )
        await session.commit()

        task = await session.get(TaskModel, task_id)
        stage = await session.get(TaskStageModel, stage_id)
        assert task is not None
        assert stage is not None

        emitted: list[dict] = []

        async def _capture_emit(_task, **kwargs):
            emitted.append(kwargs)
            return f"log-{len(emitted)}"

        monkeypatch.setattr(engine, '_emit_system_log', AsyncMock(side_effect=_capture_emit))
        close_started = AsyncMock()
        monkeypatch.setattr(engine, '_close_started_system_log', close_started)
        monkeypatch.setattr(engine, '_safe_broadcast', AsyncMock())
        monkeypatch.setattr(engine, 'notify_gate_created', AsyncMock())
        monkeypatch.setattr(engine, '_running', False)

        approved = await engine._handle_gate(
            session=session,
            task=task,
            stage=stage,
            gate_type='human_approve',
            stage_output='ready to review',
        )
        assert approved is False
        assert [item['event_type'] for item in emitted] == [
            'gate_wait_started',
            'gate_wait_cancelled',
        ]
        assert emitted[0]['status'] == 'running'
        assert emitted[1]['status'] == 'cancelled'
        close_started.assert_awaited_once()
        assert close_started.await_args.kwargs['started_log_id'] == 'log-1'
        assert close_started.await_args.kwargs['status'] == 'cancelled'

        gates = await session.execute(select(HumanGateModel).where(HumanGateModel.task_id == task_id))
        for gate in gates.scalars().all():
            await session.delete(gate)
        stage_model = await session.get(TaskStageModel, stage_id)
        if stage_model:
            await session.delete(stage_model)
        task_model = await session.get(TaskModel, task_id)
        if task_model:
            await session.delete(task_model)
        await session.commit()
