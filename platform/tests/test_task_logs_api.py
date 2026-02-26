from __future__ import annotations

from datetime import datetime
from sqlalchemy import select

import pytest
import pytest_asyncio

from app.db.session import async_session_factory
from app.models.task import TaskModel, TaskStageModel
from app.models.task_log import TaskStageLogModel


@pytest_asyncio.fixture
async def seed_task_stage_logs():
    async with async_session_factory() as session:
        task = TaskModel(id='tt-log-task', title='Task Log Test', status='running')
        stage = TaskStageModel(
            id='tt-log-stage',
            task_id='tt-log-task',
            stage_name='coding',
            agent_role='coding',
            status='running',
        )
        parse_stage = TaskStageModel(
            id='tt-log-stage-parse',
            task_id='tt-log-task',
            stage_name='parse',
            agent_role='orchestrator',
            status='completed',
        )
        session.add(task)
        session.add(stage)
        session.add(parse_stage)

        session.add_all(
            [
                TaskStageLogModel(
                    id='tt-log-1',
                    task_id='tt-log-task',
                    stage_id='tt-log-stage',
                    stage_name='coding',
                    agent_role='coding',
                    correlation_id='chat-1',
                    event_seq=1,
                    event_type='agent_runner_chat_sent',
                    event_source='llm',
                    status='running',
                    request_body={'prompt': 'write code', 'model': 'gpt-test'},
                ),
                TaskStageLogModel(
                    id='tt-log-2',
                    task_id='tt-log-task',
                    stage_id='tt-log-stage',
                    stage_name='coding',
                    agent_role='coding',
                    correlation_id='chat-1',
                    event_seq=2,
                    event_type='agent_runner_chat_received',
                    event_source='llm',
                    status='success',
                    response_body={'content': 'done'},
                ),
                TaskStageLogModel(
                    id='tt-log-3',
                    task_id='tt-log-task',
                    stage_id='tt-log-stage',
                    stage_name='coding',
                    agent_role='coding',
                    correlation_id='tool-1',
                    event_seq=3,
                    event_type='tool_call_executed',
                    event_source='tool',
                    status='success',
                    command='npm test',
                    command_args={
                        'tool_name': 'execute',
                        'command': 'npm test',
                        'cwd': '/tmp/silicon_agent/tasks/tt-log-task',
                    },
                    workspace='/tmp/silicon_agent/tasks/tt-log-task',
                    execution_mode='in_process',
                    duration_ms=123.45,
                    result='ok',
                    output_summary='ok',
                ),
                TaskStageLogModel(
                    id='tt-log-4',
                    task_id='tt-log-task',
                    stage_id='tt-log-stage-parse',
                    stage_name='parse',
                    agent_role='orchestrator',
                    event_seq=1,
                    event_type='agent_runner_chat_sent',
                    event_source='llm',
                    status='running',
                    request_body={'prompt': 'parse this'},
                ),
                TaskStageLogModel(
                    id='tt-log-5',
                    task_id='tt-log-task',
                    stage_id='tt-log-stage',
                    stage_name='coding',
                    agent_role='coding',
                    correlation_id='gate-1',
                    event_seq=4,
                    event_type='gate_wait_started',
                    event_source='system',
                    status='running',
                    response_body={'gate_type': 'human_approve'},
                ),
            ]
        )
        await session.commit()

    yield

    async with async_session_factory() as session:
        result = await session.execute(select(TaskStageLogModel).where(TaskStageLogModel.id.like('tt-log-%')))
        for item in result.scalars().all():
            await session.delete(item)

        for stage_id in ['tt-log-stage', 'tt-log-stage-parse']:
            stage_result = await session.execute(select(TaskStageModel).where(TaskStageModel.id == stage_id))
            stage = stage_result.scalar_one_or_none()
            if stage:
                await session.delete(stage)

        task_result = await session.execute(select(TaskModel).where(TaskModel.id == 'tt-log-task'))
        task = task_result.scalar_one_or_none()
        if task:
            await session.delete(task)

        await session.commit()


@pytest.mark.asyncio
async def test_list_task_logs_returns_expected_fields(client, seed_task_stage_logs):
    resp = await client.get(
        '/api/v1/task-logs',
        params={'task': 'tt-log-task', 'stage': 'coding'},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data['total'] >= 3
    assert len(data['items']) >= 3

    llm_request = next(item for item in data['items'] if item['event_type'] == 'agent_runner_chat_sent')
    llm_response = next(item for item in data['items'] if item['event_type'] == 'agent_runner_chat_received')
    tool_call = next(item for item in data['items'] if item['event_type'] == 'tool_call_executed')
    system_event = next(item for item in data['items'] if item['event_source'] == 'system')

    assert 'prompt' in llm_request['request_body']
    assert 'content' in llm_response['response_body']
    assert llm_request['status'] == 'running'
    assert llm_request['correlation_id'] == llm_response['correlation_id']
    assert llm_request['event_seq'] < llm_response['event_seq']

    assert tool_call['command'] == 'npm test'
    assert tool_call['workspace'] == '/tmp/silicon_agent/tasks/tt-log-task'
    assert tool_call['execution_mode'] == 'in_process'
    assert tool_call['duration_ms'] == 123.45
    assert tool_call['result'] == 'ok'
    assert tool_call['output_summary'] == 'ok'
    assert tool_call['command_args']['command'] == 'npm test'
    assert system_event['event_type'] == 'gate_wait_started'


@pytest.mark.asyncio
async def test_list_task_logs_pagination(client, seed_task_stage_logs):
    resp = await client.get(
        '/api/v1/task-logs',
        params={'task': 'tt-log-task', 'page': 1, 'page_size': 2},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data['page'] == 1
    assert data['page_size'] == 2
    assert data['total'] >= 3
    assert len(data['items']) <= 2


@pytest.mark.asyncio
async def test_list_task_logs_is_read_only(client, seed_task_stage_logs):
    async with async_session_factory() as session:
        before = await session.get(TaskModel, 'tt-log-task')
        assert before is not None
        before_status = before.status

    resp = await client.get(
        '/api/v1/task-logs',
        params={'task': 'tt-log-task'},
    )
    assert resp.status_code == 200

    async with async_session_factory() as session:
        after = await session.get(TaskModel, 'tt-log-task')
        assert after is not None
        assert after.status == before_status


@pytest.mark.asyncio
async def test_list_task_logs_stage_is_optional(client, seed_task_stage_logs):
    resp = await client.get('/api/v1/task-logs', params={'task': 'tt-log-task'})
    assert resp.status_code == 200
    data = resp.json()
    stages = {item['stage_name'] for item in data['items']}
    assert 'coding' in stages
    assert 'parse' in stages


@pytest.mark.asyncio
async def test_list_task_logs_orders_by_event_seq_for_same_timestamp(client):
    same_created_at = datetime(2026, 2, 24, 13, 30, 0)
    async with async_session_factory() as session:
        task = TaskModel(id='tt-log-order-task', title='Order Test', status='running')
        stage = TaskStageModel(
            id='tt-log-order-stage',
            task_id='tt-log-order-task',
            stage_name='parse',
            agent_role='orchestrator',
            status='running',
        )
        session.add(task)
        session.add(stage)
        session.add_all(
            [
                TaskStageLogModel(
                    id='tt-order-c',
                    task_id='tt-log-order-task',
                    stage_id='tt-log-order-stage',
                    stage_name='parse',
                    agent_role='orchestrator',
                    event_seq=2,
                    event_type='agent_runner_chat_sent',
                    event_source='llm',
                    status='running',
                    created_at=same_created_at,
                ),
                TaskStageLogModel(
                    id='tt-order-a',
                    task_id='tt-log-order-task',
                    stage_id='tt-log-order-stage',
                    stage_name='parse',
                    agent_role='orchestrator',
                    event_seq=1,
                    event_type='tool_call_executed',
                    event_source='tool',
                    status='success',
                    command='echo 1',
                    created_at=same_created_at,
                ),
                TaskStageLogModel(
                    id='tt-order-b',
                    task_id='tt-log-order-task',
                    stage_id='tt-log-order-stage',
                    stage_name='parse',
                    agent_role='orchestrator',
                    event_seq=3,
                    event_type='agent_runner_chat_received',
                    event_source='llm',
                    status='success',
                    created_at=same_created_at,
                ),
            ]
        )
        await session.commit()

    try:
        resp = await client.get(
            '/api/v1/task-logs',
            params={'task': 'tt-log-order-task', 'stage': 'parse'},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert [item['event_type'] for item in data['items']] == [
            'tool_call_executed',
            'agent_runner_chat_sent',
            'agent_runner_chat_received',
        ]
    finally:
        async with async_session_factory() as session:
            result = await session.execute(
                select(TaskStageLogModel).where(TaskStageLogModel.task_id == 'tt-log-order-task')
            )
            for item in result.scalars().all():
                await session.delete(item)

            stage_result = await session.execute(
                select(TaskStageModel).where(TaskStageModel.id == 'tt-log-order-stage')
            )
            stage = stage_result.scalar_one_or_none()
            if stage:
                await session.delete(stage)

            task_result = await session.execute(
                select(TaskModel).where(TaskModel.id == 'tt-log-order-task')
            )
            task = task_result.scalar_one_or_none()
            if task:
                await session.delete(task)

            await session.commit()


@pytest.mark.asyncio
async def test_list_task_logs_supports_legacy_task_id_param(client, seed_task_stage_logs):
    resp = await client.get('/api/v1/task-logs', params={'task_id': 'tt-log-task', 'stage': 'coding'})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_task_logs_requires_task_param(client):
    resp = await client.get('/api/v1/task-logs')
    assert resp.status_code == 422
