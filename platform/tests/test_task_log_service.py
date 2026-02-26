from __future__ import annotations

from sqlalchemy import select

import pytest

from app.db.session import async_session_factory
from app.models.task import TaskModel, TaskStageModel
from app.models.task_log import TaskStageLogModel
from app.services.task_log_service import TaskLogService


@pytest.mark.asyncio
async def test_update_log_preserves_fields_not_in_partial_payload():
    task_id = 'tt-log-service-task-1'
    stage_id = 'tt-log-service-stage-1'
    log_id = 'tt-log-service-log-1'

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title='Task Log Service', status='running'))
        session.add(
            TaskStageModel(
                id=stage_id,
                task_id=task_id,
                stage_name='coding',
                agent_role='coding',
                status='running',
            )
        )
        session.add(
            TaskStageLogModel(
                id=log_id,
                task_id=task_id,
                stage_id=stage_id,
                stage_name='coding',
                agent_role='coding',
                event_type='tool_call_executed',
                event_source='tool',
                event_seq=1,
                status='running',
                command='npm test',
                workspace='/tmp/ws',
                result='pending',
            )
        )
        await session.commit()

        service = TaskLogService(session)
        updated = await service.update_log(
            log_id,
            {
                'status': 'success',
                'duration_ms': 18.5,
                'result': 'ok',
            },
        )
        assert updated is True
        await session.commit()

        refreshed = await session.get(TaskStageLogModel, log_id)
        assert refreshed is not None
        assert refreshed.status == 'success'
        assert refreshed.duration_ms == 18.5
        assert refreshed.result == 'ok'
        assert refreshed.command == 'npm test'
        assert refreshed.workspace == '/tmp/ws'

        logs = await session.execute(select(TaskStageLogModel).where(TaskStageLogModel.id == log_id))
        for item in logs.scalars().all():
            await session.delete(item)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_create_log_truncates_large_result_fields():
    task_id = 'tt-log-service-task-2'
    stage_id = 'tt-log-service-stage-2'
    log_id = 'tt-log-service-log-2'
    large_text = 'x' * 60000

    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title='Task Log Truncation', status='running'))
        session.add(
            TaskStageModel(
                id=stage_id,
                task_id=task_id,
                stage_name='coding',
                agent_role='coding',
                status='running',
            )
        )
        await session.commit()

        service = TaskLogService(session)
        await service.create_log(
            {
                'id': log_id,
                'task_id': task_id,
                'stage_id': stage_id,
                'stage_name': 'coding',
                'agent_role': 'coding',
                'event_seq': 1,
                'event_type': 'tool_call_executed',
                'event_source': 'tool',
                'status': 'failed',
                'result': large_text,
                'output_summary': large_text,
            }
        )
        await session.commit()

        created = await session.get(TaskStageLogModel, log_id)
        assert created is not None
        assert created.output_truncated is True
        assert created.result is not None and created.result.endswith('...[truncated]')
        assert created.output_summary is not None and created.output_summary.endswith('...[truncated]')

        logs = await session.execute(select(TaskStageLogModel).where(TaskStageLogModel.id == log_id))
        for item in logs.scalars().all():
            await session.delete(item)
        stage = await session.get(TaskStageModel, stage_id)
        if stage:
            await session.delete(stage)
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        await session.commit()
