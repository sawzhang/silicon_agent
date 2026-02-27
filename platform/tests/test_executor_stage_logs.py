from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import executor


class _FakeEvents:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[object, str | None]]] = {}

    def on(self, event_name: str, handler, source: str | None = None) -> None:
        self._handlers.setdefault(event_name, []).append((handler, source))

    def off_by_source(self, source: str) -> None:
        for event_name in list(self._handlers.keys()):
            self._handlers[event_name] = [
                (handler, handler_source)
                for handler, handler_source in self._handlers[event_name]
                if handler_source != source
            ]

    async def emit(self, event_name: str, event: object) -> None:
        for handler, _ in list(self._handlers.get(event_name, [])):
            result = handler(event)
            if inspect.isawaitable(result):
                await result


class _FakeRunner:
    def __init__(self, *, with_tool: bool = False) -> None:
        self.events = _FakeEvents()
        self.config = SimpleNamespace(model='test-model')
        self.cumulative_usage = SimpleNamespace(total_tokens=321)
        self.default_cwd = '/tmp/test-workspace'
        self._with_tool = with_tool

    def reset_usage(self) -> None:
        return None

    async def chat(self, _prompt: str, reset: bool = True, **_: object):
        await self.events.emit(
            'turn_start',
            SimpleNamespace(turn=0, message_count=2),
        )

        if self._with_tool:
            tool_call_id = 'tool-call-1'
            tool_name = 'execute'
            args = {'command': 'echo hi', 'cwd': '/tmp/test-workspace'}
            await self.events.emit(
                'before_tool_call',
                SimpleNamespace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=args,
                    turn=0,
                ),
            )
            await self.events.emit(
                'tool_execution_update',
                SimpleNamespace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    output='line-1\n',
                    turn=0,
                ),
            )
            await self.events.emit(
                'tool_execution_update',
                SimpleNamespace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    output='line-2\n',
                    turn=0,
                ),
            )
            await self.events.emit(
                'after_tool_result',
                SimpleNamespace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=args,
                    result='tool-ok',
                    turn=0,
                ),
            )
            await self.events.emit(
                'turn_end',
                SimpleNamespace(
                    turn=0,
                    has_tool_calls=True,
                    tool_call_count=1,
                    content='tool done',
                ),
            )
        else:
            await self.events.emit(
                'turn_end',
                SimpleNamespace(
                    turn=0,
                    has_tool_calls=False,
                    tool_call_count=0,
                    content='stage output',
                ),
            )

        return SimpleNamespace(text_content='stage output')


class _FakePipeline:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self._counter = 0

    async def emit_create(self, **kwargs):
        self._counter += 1
        log_id = kwargs.get('log_id') or f'log-{self._counter}'
        self.created.append({'log_id': log_id, **kwargs})
        return log_id

    async def emit_update(self, *, log_id: str, updates: dict, priority: str = 'normal'):
        self.updated.append(
            {
                'log_id': log_id,
                'updates': updates,
                'priority': priority,
            }
        )
        return True


class _CancelledRunner(_FakeRunner):
    async def chat(self, _prompt: str, reset: bool = True, **_: object):
        await self.events.emit(
            'turn_start',
            SimpleNamespace(turn=0, message_count=1),
        )
        raise asyncio.CancelledError()


class _FlakyUpdatePipeline(_FakePipeline):
    def __init__(self) -> None:
        super().__init__()
        self._fail_log_id: str | None = None
        self._failed_once = False

    async def emit_create(self, **kwargs):
        log_id = await super().emit_create(**kwargs)
        if kwargs.get('event_type') == 'agent_runner_chat_sent' and self._fail_log_id is None:
            self._fail_log_id = log_id
        return log_id

    async def emit_update(self, *, log_id: str, updates: dict, priority: str = 'normal'):
        if (
            self._fail_log_id
            and log_id == self._fail_log_id
            and not self._failed_once
        ):
            self._failed_once = True
            raise asyncio.CancelledError()
        return await super().emit_update(log_id=log_id, updates=updates, priority=priority)


class _FakeSandboxManager:
    def __init__(self, result) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def execute_stage(self, info, **kwargs):
        self.calls.append({"info": info, **kwargs})
        return self._result


@pytest.mark.asyncio
async def test_execute_stage_emits_chat_sent_and_received(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-1',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-1',
        stage_name='doc',
        agent_role='doc',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )

    fake_pipeline = _FakePipeline()
    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: fake_pipeline)
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=None))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(executor, 'build_user_prompt', lambda _ctx: 'prompt')
    monkeypatch.setattr(
        executor,
        'get_agent',
        lambda _role, _task_id, model=None, max_turns=None, extra_skill_dirs=None, system_prompt_append=None: _FakeRunner(),
    )

    result = await executor.execute_stage(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
    )

    assert result == 'stage output'
    llm_events = [
        item
        for item in fake_pipeline.created
        if item['event_type'] in {'agent_runner_chat_sent', 'agent_runner_chat_received'}
    ]
    assert [item['event_type'] for item in llm_events] == [
        'agent_runner_chat_sent',
        'agent_runner_chat_received',
    ]
    assert llm_events[0]['status'] == 'running'
    assert llm_events[1]['status'] == 'success'
    assert llm_events[0]['correlation_id'] == llm_events[1]['correlation_id']
    assert llm_events[0]['request_body']['prompt'] == 'prompt'
    assert llm_events[1]['response_body']['content'] == 'stage output'


@pytest.mark.asyncio
async def test_execute_stage_uses_agent_config_runtime_overrides(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-2',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-2',
        stage_name='coding',
        agent_role='coding',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )
    db_agent = SimpleNamespace(
        role='coding',
        model_name='gpt-5.1-codex-mini',
        config={
            'max_turns': 18,
            'extra_skill_dirs': ['/tmp/skills'],
            'system_prompt_append': 'extra prompt',
            'temperature': 0.2,
            'max_tokens': 1200,
        },
    )

    captured_params: dict[str, object] = {}

    class _ChatRunner(_FakeRunner):
        async def chat(self, _prompt: str, reset: bool = True, temperature=None, max_tokens=None):
            captured_params['temperature'] = temperature
            captured_params['max_tokens'] = max_tokens
            return await super().chat(_prompt, reset=reset)

    def _capture_runner(
        _role,
        _task_id,
        model=None,
        max_turns=None,
        extra_skill_dirs=None,
        system_prompt_append=None,
    ):
        captured_params['model'] = model
        captured_params['max_turns'] = max_turns
        captured_params['extra_skill_dirs'] = extra_skill_dirs
        captured_params['system_prompt_append'] = system_prompt_append
        return _ChatRunner()

    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: _FakePipeline())
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=db_agent))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(executor, 'build_user_prompt', lambda _ctx: 'prompt')
    monkeypatch.setattr(executor, 'get_agent', _capture_runner)

    result = await executor.execute_stage(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
    )

    assert result == 'stage output'
    assert captured_params['model'] == 'gpt-5.1-codex-mini'
    assert captured_params['max_turns'] == 18
    assert captured_params['extra_skill_dirs'] == ['/tmp/skills']
    assert captured_params['system_prompt_append'] == 'extra prompt'
    assert captured_params['temperature'] == 0.2
    assert captured_params['max_tokens'] == 1200


@pytest.mark.asyncio
async def test_execute_stage_tool_single_record_lifecycle(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-3',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-3',
        stage_name='coding',
        agent_role='coding',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )

    fake_pipeline = _FakePipeline()
    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: fake_pipeline)
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=None))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(executor, 'build_user_prompt', lambda _ctx: 'prompt')
    monkeypatch.setattr(
        executor,
        'get_agent',
        lambda _role, _task_id, model=None, max_turns=None, extra_skill_dirs=None, system_prompt_append=None: _FakeRunner(with_tool=True),
    )

    result = await executor.execute_stage(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
    )

    assert result == 'stage output'
    tool_creates = [item for item in fake_pipeline.created if item['event_type'] == 'tool_call_executed']
    assert len(tool_creates) == 1
    tool_create = tool_creates[0]
    assert tool_create['status'] == 'running'
    assert tool_create['command'] == 'echo hi'
    assert tool_create['workspace'] == '/tmp/test-workspace'
    assert tool_create['execution_mode'] == 'in_process'

    tool_update = next(
        item for item in fake_pipeline.updated if item['log_id'] == tool_create['log_id']
    )
    assert tool_update['log_id'] == tool_create['log_id']
    assert tool_update['updates']['status'] == 'success'
    assert tool_update['updates']['result'] == 'tool-ok'
    assert tool_update['updates']['output_summary'] == 'line-1\nline-2\n'
    assert isinstance(tool_update['updates']['duration_ms'], float)

    # LLM started/sent lifecycle records should be finalized with duration.
    started_llm_logs = [
        item
        for item in fake_pipeline.created
        if item['event_type'] in {'agent_runner_chat_sent', 'llm_turn_sent'}
    ]
    assert started_llm_logs
    for started in started_llm_logs:
        update = next(
            item for item in fake_pipeline.updated if item['log_id'] == started['log_id']
        )
        assert update['updates']['status'] == 'success'
        assert isinstance(update['updates']['duration_ms'], float)


@pytest.mark.asyncio
async def test_execute_stage_cancellation_still_finalizes_started_logs(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-4',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-4',
        stage_name='coding',
        agent_role='coding',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )

    fake_pipeline = _FlakyUpdatePipeline()
    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: fake_pipeline)
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=None))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(executor, 'build_user_prompt', lambda _ctx: 'prompt')
    monkeypatch.setattr(
        executor,
        'get_agent',
        lambda _role, _task_id, model=None, max_turns=None, extra_skill_dirs=None, system_prompt_append=None: _CancelledRunner(),
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute_stage(
            session=session,
            task=task,
            stage=stage,
            prior_outputs=[],
        )

    chat_start = next(item for item in fake_pipeline.created if item['event_type'] == 'agent_runner_chat_sent')
    chat_updates = [item for item in fake_pipeline.updated if item['log_id'] == chat_start['log_id']]
    assert chat_updates
    assert chat_updates[-1]['updates']['status'] == 'cancelled'
    assert isinstance(chat_updates[-1]['updates']['duration_ms'], float)

    turn_start = next(item for item in fake_pipeline.created if item['event_type'] == 'llm_turn_sent')
    turn_updates = [item for item in fake_pipeline.updated if item['log_id'] == turn_start['log_id']]
    assert turn_updates
    assert turn_updates[-1]['updates']['status'] == 'cancelled'
    assert isinstance(turn_updates[-1]['updates']['duration_ms'], float)


@pytest.mark.asyncio
async def test_execute_stage_sandboxed_emits_standardized_pipeline_events(monkeypatch):
    from app.worker import agents as worker_agents
    from app.worker import prompts as worker_prompts
    from app.worker import sandbox as worker_sandbox

    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-sandbox-1',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-sandbox-1',
        stage_name='coding',
        agent_role='coding',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )
    sandbox_info = SimpleNamespace(container_name='sbx-1')

    fake_pipeline = _FakePipeline()
    fake_sandbox_result = SimpleNamespace(
        text_content='sandbox output',
        total_tokens=99,
        tool_calls=[
            {
                'tool_name': 'execute',
                'args': {'command': 'echo sandbox', 'cwd': '/workspace'},
                'duration_ms': 12.5,
                'result_preview': 'ok',
                'status': 'success',
                'tool_call_id': 'tc-1',
            }
        ],
        error=None,
    )
    fake_sandbox_mgr = _FakeSandboxManager(fake_sandbox_result)

    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: fake_pipeline)
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=None))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(worker_agents, 'resolve_model_for_role', lambda _role, _model: 'sandbox-model')
    monkeypatch.setattr(worker_agents, 'ROLE_TOOLS', {'coding': {'execute'}})
    monkeypatch.setattr(worker_agents, '_get_skill_dirs', lambda _role: [])
    monkeypatch.setattr(worker_prompts, 'build_user_prompt', lambda _ctx: 'sandbox prompt')
    monkeypatch.setattr(worker_prompts, 'SYSTEM_PROMPTS', {'coding': 'system', 'orchestrator': 'system'})
    monkeypatch.setattr(worker_sandbox, 'get_sandbox_manager', lambda: fake_sandbox_mgr)

    result = await executor.execute_stage_sandboxed(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
        sandbox_info=sandbox_info,
    )

    assert result == 'sandbox output'
    assert fake_sandbox_mgr.calls
    assert task.total_tokens == 99

    llm_events = [
        item
        for item in fake_pipeline.created
        if item['event_type'] in {'agent_runner_chat_sent', 'agent_runner_chat_received'}
    ]
    assert [item['event_type'] for item in llm_events] == [
        'agent_runner_chat_sent',
        'agent_runner_chat_received',
    ]
    assert llm_events[0]['status'] == 'running'
    assert llm_events[1]['status'] == 'success'
    assert llm_events[0]['correlation_id'] == llm_events[1]['correlation_id']
    assert llm_events[0]['request_body']['prompt'] == 'sandbox prompt'

    tool_event = next(item for item in fake_pipeline.created if item['event_type'] == 'tool_call_executed')
    assert tool_event['event_source'] == 'tool'
    assert tool_event['status'] == 'success'
    assert tool_event['command'] == 'echo sandbox'
    assert tool_event['workspace'] == '/workspace'
    assert tool_event['execution_mode'] == 'sandbox'
    assert tool_event['duration_ms'] == 12.5
    assert tool_event['result'] == 'ok'


@pytest.mark.asyncio
async def test_execute_stage_sandboxed_error_emits_failed_chat_received(monkeypatch):
    from app.worker import agents as worker_agents
    from app.worker import prompts as worker_prompts
    from app.worker import sandbox as worker_sandbox

    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-sandbox-2',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-sandbox-2',
        stage_name='coding',
        agent_role='coding',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )
    sandbox_info = SimpleNamespace(container_name='sbx-2')

    fake_pipeline = _FakePipeline()
    fake_sandbox_result = SimpleNamespace(
        text_content='',
        total_tokens=0,
        tool_calls=[],
        error='sandbox boom',
    )
    fake_sandbox_mgr = _FakeSandboxManager(fake_sandbox_result)

    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: fake_pipeline)
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=None))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(worker_agents, 'resolve_model_for_role', lambda _role, _model: 'sandbox-model')
    monkeypatch.setattr(worker_agents, 'ROLE_TOOLS', {'coding': {'execute'}})
    monkeypatch.setattr(worker_agents, '_get_skill_dirs', lambda _role: [])
    monkeypatch.setattr(worker_prompts, 'build_user_prompt', lambda _ctx: 'sandbox prompt')
    monkeypatch.setattr(worker_prompts, 'SYSTEM_PROMPTS', {'coding': 'system', 'orchestrator': 'system'})
    monkeypatch.setattr(worker_sandbox, 'get_sandbox_manager', lambda: fake_sandbox_mgr)

    with pytest.raises(RuntimeError, match='sandbox boom'):
        await executor.execute_stage_sandboxed(
            session=session,
            task=task,
            stage=stage,
            prior_outputs=[],
            sandbox_info=sandbox_info,
        )

    llm_events = [
        item
        for item in fake_pipeline.created
        if item['event_type'] in {'agent_runner_chat_sent', 'agent_runner_chat_received'}
    ]
    assert [item['event_type'] for item in llm_events] == [
        'agent_runner_chat_sent',
        'agent_runner_chat_received',
    ]
    assert llm_events[1]['status'] == 'failed'
    assert llm_events[1]['response_body']['error'] == 'sandbox boom'


@pytest.mark.asyncio
async def test_execute_stage_sandboxed_uses_agent_model_override_when_stage_model_missing(monkeypatch):
    from app.worker import agents as worker_agents
    from app.worker import prompts as worker_prompts
    from app.worker import sandbox as worker_sandbox

    session = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(
        id='task-sandbox-3',
        title='task title',
        description='task description',
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    stage = SimpleNamespace(
        id='stage-sandbox-3',
        stage_name='coding',
        agent_role='coding',
        status='pending',
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        tokens_used=0,
        output_summary=None,
    )
    sandbox_info = SimpleNamespace(container_name='sbx-3')
    db_agent = SimpleNamespace(
        role='coding',
        status='idle',
        current_task_id=None,
        started_at=None,
        last_active_at=None,
        model_name='agent-config-model',
        config={},
    )

    fake_pipeline = _FakePipeline()
    fake_sandbox_result = SimpleNamespace(
        text_content='sandbox output',
        total_tokens=17,
        tool_calls=[],
        error=None,
    )
    fake_sandbox_mgr = _FakeSandboxManager(fake_sandbox_result)

    monkeypatch.setattr(executor, 'get_task_log_pipeline', lambda: fake_pipeline)
    monkeypatch.setattr(executor, '_get_agent', AsyncMock(return_value=db_agent))
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(worker_agents, 'resolve_model_for_role', lambda _role, model: model or 'role-map-model')
    monkeypatch.setattr(worker_agents, 'ROLE_TOOLS', {'coding': {'execute'}})
    monkeypatch.setattr(worker_agents, '_get_skill_dirs', lambda _role: [])
    monkeypatch.setattr(worker_prompts, 'build_user_prompt', lambda _ctx: 'sandbox prompt')
    monkeypatch.setattr(worker_prompts, 'SYSTEM_PROMPTS', {'coding': 'system', 'orchestrator': 'system'})
    monkeypatch.setattr(worker_sandbox, 'get_sandbox_manager', lambda: fake_sandbox_mgr)

    result = await executor.execute_stage_sandboxed(
        session=session,
        task=task,
        stage=stage,
        prior_outputs=[],
        sandbox_info=sandbox_info,
    )

    assert result == 'sandbox output'
    assert fake_sandbox_mgr.calls
    assert fake_sandbox_mgr.calls[0]['model'] == 'agent-config-model'
