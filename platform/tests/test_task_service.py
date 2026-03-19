"""Mock-session unit tests for TaskService.

All session.execute() / session.get() calls are mocked so every line after
`await` is covered within the same trace context — fixing the coverage.py /
Python 3.13 sys.monitoring coroutine-resume blind-spot.

Covered previously-uncovered lines:
  66-74:   list_tasks return path (count, ordering, pagination)
  95-121:  create_task with template_id branch (stage creation + re-fetch)
  133-136: get_task found / None paths
  144-145: get_stages result
  157-166: cancel_task paths (none, already-terminal, pending→cancelled)
  178-182: decompose_prd project context building
  196:     project_context appended to user_content
  257:     batch_create return
  282-312: retry_task full path
  329-332: retry_from_stage task-not-found + non-failed-task raise
  335-354: retry_from_stage stage validation + success
  370-406: retry_batch full loop
  426:     _load_task_with_relations_optional return None
  431-433: _load_task_with_relations raise
  445-446: _resolve_stage_max_retries JSON decode error fallback
  459:     _select_retryable_failed_stage no failed stages
  466-467: _select_retryable_failed_stage template order_map build error
  474:     _select_retryable_failed_stage all-stages-retry-limit exhausted
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.task_service import TaskService
from app.schemas.task import (
    TaskBatchCreateRequest,
    TaskCreateRequest,
    TaskDecomposeRequest,
    BatchTaskItem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> AsyncMock:
    """Return an AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    return session


def _mock_result(
    *,
    scalar=None,
    scalar_one=None,
    scalar_one_or_none=None,
    scalars_all=None,
):
    """Build a mock object returned by session.execute()."""
    r = MagicMock()
    r.scalar.return_value = scalar
    r.scalar_one.return_value = scalar_one
    r.scalar_one_or_none.return_value = scalar_one_or_none
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all if scalars_all is not None else []
    r.scalars.return_value = scalars_mock
    return r


def _make_stage(
    *,
    id: str = "stage-1",
    task_id: str = "task-1",
    stage_name: str = "coding",
    agent_role: str = "coding",
    status: str = "pending",
    retry_count: int = 0,
    tokens_used: int = 0,
    error_message=None,
    failure_category=None,
    started_at=None,
    completed_at=None,
    duration_seconds=None,
    output_summary=None,
    output_structured=None,
    self_assessment_score=None,
    turns_used: int = 0,
    self_fix_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        task_id=task_id,
        stage_name=stage_name,
        agent_role=agent_role,
        status=status,
        retry_count=retry_count,
        tokens_used=tokens_used,
        error_message=error_message,
        failure_category=failure_category,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
        output_summary=output_summary,
        output_structured=output_structured,
        self_assessment_score=self_assessment_score,
        turns_used=turns_used,
        self_fix_count=self_fix_count,
    )


def _make_task(
    *,
    id: str = "task-1",
    title: str = "Test Task",
    description: str | None = None,
    status: str = "pending",
    jira_id: str | None = None,
    template_id: str | None = None,
    project_id: str | None = None,
    target_branch: str | None = None,
    yunxiao_task_id: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    total_tokens: int = 0,
    total_cost_rmb: float = 0.0,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    stages: list | None = None,
    template=None,
    project=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        title=title,
        description=description,
        status=status,
        jira_id=jira_id,
        template_id=template_id,
        project_id=project_id,
        target_branch=target_branch,
        yunxiao_task_id=yunxiao_task_id,
        branch_name=branch_name,
        pr_url=pr_url,
        total_tokens=total_tokens,
        total_cost_rmb=total_cost_rmb,
        created_at=created_at or datetime.now(timezone.utc),
        completed_at=completed_at,
        stages=stages if stages is not None else [],
        template=template,
        project=project,
    )


def _make_template(
    *,
    id: str = "tmpl-1",
    stages_json: str | None = None,
    display_name: str = "Template 1",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        stages=stages_json,
        display_name=display_name,
        name="template_1",
    )


# ---------------------------------------------------------------------------
# list_tasks (lines 66-74)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_returns_total_and_items():
    """Lines 66-74: list_tasks returns correct total and items."""
    session = _make_session()
    task = _make_task(id="t1", title="Hello")
    session.execute.side_effect = [
        _mock_result(scalar=5),             # count query
        _mock_result(scalars_all=[task]),   # tasks query
    ]
    svc = TaskService(session)
    result = await svc.list_tasks()
    assert result.total == 5
    assert len(result.items) == 1
    assert result.items[0].id == "t1"
    assert result.page == 1
    assert result.page_size == 20


@pytest.mark.asyncio
async def test_list_tasks_pagination():
    """Lines 68-79: pagination offset/limit applied."""
    session = _make_session()
    session.execute.side_effect = [
        _mock_result(scalar=100),
        _mock_result(scalars_all=[]),
    ]
    svc = TaskService(session)
    result = await svc.list_tasks(page=3, page_size=10)
    assert result.page == 3
    assert result.page_size == 10
    assert result.total == 100


@pytest.mark.asyncio
async def test_list_tasks_empty_total_defaults_to_zero():
    """Lines 66: scalar() returning None defaults to 0."""
    session = _make_session()
    session.execute.side_effect = [
        _mock_result(scalar=None),   # None → 0
        _mock_result(scalars_all=[]),
    ]
    svc = TaskService(session)
    result = await svc.list_tasks()
    assert result.total == 0


@pytest.mark.asyncio
async def test_list_tasks_with_status_filter():
    """Lines 50-52: status filter applied."""
    session = _make_session()
    task = _make_task(status="running")
    session.execute.side_effect = [
        _mock_result(scalar=1),
        _mock_result(scalars_all=[task]),
    ]
    svc = TaskService(session)
    result = await svc.list_tasks(status="running")
    assert result.total == 1
    assert result.items[0].status == "running"


@pytest.mark.asyncio
async def test_list_tasks_with_project_id_filter():
    """Lines 54-57: project_id filter applied."""
    session = _make_session()
    task = _make_task(project_id="proj-1")
    session.execute.side_effect = [
        _mock_result(scalar=1),
        _mock_result(scalars_all=[task]),
    ]
    svc = TaskService(session)
    result = await svc.list_tasks(project_id="  proj-1  ")
    assert result.total == 1


@pytest.mark.asyncio
async def test_list_tasks_with_title_filter():
    """Lines 59-63: title filter applied (case-insensitive LIKE)."""
    session = _make_session()
    task = _make_task(title="My Special Task")
    session.execute.side_effect = [
        _mock_result(scalar=1),
        _mock_result(scalars_all=[task]),
    ]
    svc = TaskService(session)
    result = await svc.list_tasks(title="  special  ")
    assert result.total == 1


# ---------------------------------------------------------------------------
# create_task (lines 95-121)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_without_template():
    """Lines 81-121: create_task without template_id."""
    session = _make_session()
    created_task = _make_task(
        id="new-task-1",
        title="No Template",
        target_branch="silicon_agent/1",
    )
    session.execute.return_value = _mock_result(scalar_one=created_task)
    svc = TaskService(session)
    result = await svc.create_task(TaskCreateRequest(title="No Template"))
    assert result.id == "new-task-1"
    assert result.target_branch == "silicon_agent/1"
    session.add.assert_called()
    session.flush.assert_called_once()
    session.commit.assert_called_once()
    created_model = session.add.call_args_list[0].args[0]
    assert created_model.target_branch == f"silicon_agent/{created_model.id.rsplit('-', 1)[-1]}"


@pytest.mark.asyncio
async def test_create_task_with_template_creates_stages():
    """Lines 95-121: create_task with template_id fetches template and creates stages."""
    session = _make_session()
    stages_json = json.dumps([
        {"name": "spec", "agent_role": "spec"},
        {"name": "coding", "agent_role": "coding"},
    ])
    template = _make_template(id="tmpl-1", stages_json=stages_json)
    # session.get returns the template
    session.get.return_value = template

    created_task = _make_task(id="new-task-2", title="With Template", template_id="tmpl-1",
                              template=template)
    session.execute.return_value = _mock_result(scalar_one=created_task)

    svc = TaskService(session)
    result = await svc.create_task(
        TaskCreateRequest(title="With Template", template_id="tmpl-1")
    )
    assert result.id == "new-task-2"
    # Two stage adds + one task add = 3 add calls
    assert session.add.call_count >= 3
    session.flush.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_create_task_with_template_no_stages_json():
    """Lines 97: template.stages is None/empty → no stages created."""
    session = _make_session()
    template = _make_template(id="tmpl-2", stages_json=None)
    session.get.return_value = template

    created_task = _make_task(id="new-task-3", title="Empty Template", template_id="tmpl-2")
    session.execute.return_value = _mock_result(scalar_one=created_task)

    svc = TaskService(session)
    result = await svc.create_task(
        TaskCreateRequest(title="Empty Template", template_id="tmpl-2")
    )
    assert result.id == "new-task-3"
    # Only task add, no stage adds
    assert session.add.call_count == 1


@pytest.mark.asyncio
async def test_create_task_template_not_found():
    """Lines 96-97: template not found in DB → no stages created."""
    session = _make_session()
    session.get.return_value = None  # template not found

    created_task = _make_task(id="new-task-4", title="Missing Template")
    session.execute.return_value = _mock_result(scalar_one=created_task)

    svc = TaskService(session)
    result = await svc.create_task(
        TaskCreateRequest(title="Missing Template", template_id="missing-tmpl")
    )
    assert result.id == "new-task-4"


# ---------------------------------------------------------------------------
# clone_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clone_task_not_found():
    """clone_task returns None when source task is missing."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)

    result = await svc.clone_task("missing-task")

    assert result is None


@pytest.mark.asyncio
async def test_clone_task_reuses_create_task_with_whitelisted_fields():
    """clone_task should create a fresh task from copy-safe source fields only."""
    session = _make_session()
    source = _make_task(
        id="task-source",
        title="Clone Me",
        description="Original task body",
        status="failed",
        jira_id="JIRA-1",
        template_id="tmpl-1",
        project_id="proj-1",
        target_branch="silicon_agent/source",
        yunxiao_task_id="YX-1",
        branch_name="feature/source",
        pr_url="https://example.com/pr/1",
    )
    session.execute.return_value = _mock_result(scalar_one_or_none=source)
    svc = TaskService(session)
    cloned = _make_task(
        id="task-clone",
        title="Clone Me",
        description="Original task body",
        status="pending",
        jira_id="JIRA-1",
        template_id="tmpl-1",
        project_id="proj-1",
        target_branch="silicon_agent/clone",
        yunxiao_task_id="YX-1",
    )
    svc.create_task = AsyncMock(return_value=svc._task_to_response(cloned))

    result = await svc.clone_task("task-source")

    assert result.id == "task-clone"
    svc.create_task.assert_awaited_once()
    request = svc.create_task.await_args.args[0]
    assert isinstance(request, TaskCreateRequest)
    assert request.title == "Clone Me"
    assert request.description == "Original task body"
    assert request.jira_id == "JIRA-1"
    assert request.template_id == "tmpl-1"
    assert request.project_id == "proj-1"
    assert request.yunxiao_task_id == "YX-1"
    assert request.target_branch is None


# ---------------------------------------------------------------------------
# get_task (lines 133-136)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_found():
    """Lines 133-136: get_task returns TaskDetailResponse when task exists."""
    session = _make_session()
    task = _make_task(id="found-1", title="Found Task")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.get_task("found-1")
    assert result is not None
    assert result.id == "found-1"


@pytest.mark.asyncio
async def test_get_task_not_found():
    """Lines 134-135: get_task returns None when task doesn't exist."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    result = await svc.get_task("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_task_sorts_stages_by_template_order():
    """Task detail stages should follow template-defined order."""
    session = _make_session()
    template = _make_template(
        id="tmpl-order",
        stages_json=json.dumps(
            [
                {"name": "parse", "agent_role": "orchestrator", "order": 0},
                {"name": "code", "agent_role": "coding", "order": 1},
                {"name": "test", "agent_role": "test", "order": 2},
                {"name": "signoff", "agent_role": "orchestrator", "order": 3},
            ]
        ),
    )
    task = _make_task(
        id="task-order",
        template=template,
        stages=[
            _make_stage(id="s-code", stage_name="code", agent_role="coding"),
            _make_stage(id="s-signoff", stage_name="signoff", agent_role="orchestrator"),
            _make_stage(id="s-parse", stage_name="parse", agent_role="orchestrator"),
            _make_stage(id="s-test", stage_name="test", agent_role="test"),
        ],
    )
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)

    result = await svc.get_task("task-order")

    assert result is not None
    assert [s.stage_name for s in result.stages] == ["parse", "code", "test", "signoff"]


@pytest.mark.asyncio
async def test_get_task_unknown_stages_are_sorted_last_and_stable():
    """Unknown stage names should be placed after known stages with stable fallback order."""
    session = _make_session()
    template = _make_template(
        id="tmpl-order",
        stages_json=json.dumps(
            [
                {"name": "parse", "agent_role": "orchestrator", "order": 0},
                {"name": "code", "agent_role": "coding", "order": 1},
            ]
        ),
    )
    task = _make_task(
        id="task-order-unknown",
        template=template,
        stages=[
            _make_stage(id="z-last", stage_name="custom_b", agent_role="coding"),
            _make_stage(id="s-parse", stage_name="parse", agent_role="orchestrator"),
            _make_stage(id="a-first", stage_name="custom_a", agent_role="coding"),
        ],
    )
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)

    result = await svc.get_task("task-order-unknown")

    assert result is not None
    assert [s.stage_name for s in result.stages] == ["parse", "custom_a", "custom_b"]
    assert [s.id for s in result.stages] == ["s-parse", "a-first", "z-last"]


# ---------------------------------------------------------------------------
# get_stages (lines 144-145)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stages_returns_list():
    """Lines 144-145: get_stages returns list of TaskStageResponse."""
    session = _make_session()
    stage1 = _make_stage(id="s1", task_id="t1", stage_name="spec", agent_role="spec")
    stage2 = _make_stage(id="s2", task_id="t1", stage_name="coding", agent_role="coding")
    session.execute.return_value = _mock_result(scalars_all=[stage1, stage2])
    svc = TaskService(session)
    result = await svc.get_stages("t1")
    assert len(result) == 2
    assert result[0].stage_name == "spec"
    assert result[1].stage_name == "coding"


@pytest.mark.asyncio
async def test_get_stages_empty():
    """Lines 144-145: get_stages returns empty list when no stages."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalars_all=[])
    svc = TaskService(session)
    result = await svc.get_stages("t1")
    assert result == []


# ---------------------------------------------------------------------------
# cancel_task (lines 157-166)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_not_found():
    """Lines 158-159: cancel_task returns None when task not found."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    result = await svc.cancel_task("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cancel_task_already_completed():
    """Lines 160-161: cancel_task returns response unchanged for already-terminal tasks."""
    session = _make_session()
    task = _make_task(id="t1", status="completed")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.cancel_task("t1")
    assert result is not None
    assert result.status == "completed"
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_task_already_failed():
    """Lines 160-161: cancel_task returns response unchanged for failed tasks."""
    session = _make_session()
    task = _make_task(id="t1", status="failed")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.cancel_task("t1")
    assert result.status == "failed"
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_task_pending_sets_cancelled():
    """Lines 162-166: cancel_task transitions pending→cancelled and commits."""
    session = _make_session()
    task = _make_task(id="t1", status="pending")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.cancel_task("t1")
    assert task.status == "cancelled"
    assert task.completed_at is not None
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(task)
    assert result is not None


@pytest.mark.asyncio
async def test_cancel_task_running_sets_cancelled():
    """Lines 162-166: cancel_task transitions running→cancelled."""
    session = _make_session()
    task = _make_task(id="t1", status="running")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    await svc.cancel_task("t1")
    assert task.status == "cancelled"


# ---------------------------------------------------------------------------
# decompose_prd (lines 178-196)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_prd_without_project():
    """decompose_prd with no project_id skips session.get call."""
    session = _make_session()
    fake_response = SimpleNamespace(
        content='{"tasks": [{"title": "T1", "description": "D1", "priority": "high"}], "summary": "S"}',
        total_tokens=100,
    )
    with patch("app.integration.llm_client.get_llm_client") as mock_get_llm:
        mock_client = AsyncMock()
        mock_client.chat.return_value = fake_response
        mock_get_llm.return_value = mock_client

        svc = TaskService(session)
        result = await svc.decompose_prd(TaskDecomposeRequest(prd_text="some PRD"))

    assert len(result.tasks) == 1
    assert result.tasks[0].title == "T1"
    assert result.summary == "S"
    assert result.tokens_used == 100
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_decompose_prd_with_project_tech_stack_and_repo_tree():
    """Lines 178-196: decompose_prd builds project context from tech_stack and repo_tree."""
    session = _make_session()
    project = SimpleNamespace(
        id="proj-1",
        tech_stack=["Python", "FastAPI"],
        repo_tree="src/\n  app.py",
    )
    session.get.return_value = project

    fake_response = SimpleNamespace(
        content='{"tasks": [{"title": "API Task", "description": "Implement API", "priority": "medium"}], "summary": "One task"}',
        total_tokens=200,
    )

    captured_messages = []

    async def capture_chat(messages, **kwargs):
        captured_messages.extend(messages)
        return fake_response

    with patch("app.integration.llm_client.get_llm_client") as mock_get_llm:
        mock_client = AsyncMock()
        mock_client.chat.side_effect = capture_chat
        mock_get_llm.return_value = mock_client

        svc = TaskService(session)
        result = await svc.decompose_prd(
            TaskDecomposeRequest(prd_text="Build an API", project_id="proj-1")
        )

    # Verify session.get was called with correct args
    session.get.assert_called_once()

    # Verify the user_content included project context (line 196)
    user_msg = next(m for m in captured_messages if m.role == "user")
    assert "项目技术栈" in user_msg.content
    assert "Python" in user_msg.content
    assert "FastAPI" in user_msg.content
    assert "项目目录结构" in user_msg.content
    assert "src/" in user_msg.content
    assert "项目上下文" in user_msg.content

    assert len(result.tasks) == 1
    assert result.tasks[0].title == "API Task"


@pytest.mark.asyncio
async def test_decompose_prd_with_project_no_tech_stack_no_repo_tree():
    """Lines 178-184: project without tech_stack/repo_tree → no context added."""
    session = _make_session()
    project = SimpleNamespace(id="proj-2", tech_stack=None, repo_tree=None)
    session.get.return_value = project

    fake_response = SimpleNamespace(
        content='{"tasks": [], "summary": "Nothing"}',
        total_tokens=50,
    )

    captured_messages = []

    async def capture_chat(messages, **kwargs):
        captured_messages.extend(messages)
        return fake_response

    with patch("app.integration.llm_client.get_llm_client") as mock_get_llm:
        mock_client = AsyncMock()
        mock_client.chat.side_effect = capture_chat
        mock_get_llm.return_value = mock_client

        svc = TaskService(session)
        result = await svc.decompose_prd(
            TaskDecomposeRequest(prd_text="PRD text", project_id="proj-2")
        )

    user_msg = next(m for m in captured_messages if m.role == "user")
    # No project context appended since tech_stack and repo_tree are None
    assert "项目上下文" not in user_msg.content
    assert result.summary == "Nothing"


@pytest.mark.asyncio
async def test_decompose_prd_project_not_found():
    """Lines 178: project_id given but project not found → no context."""
    session = _make_session()
    session.get.return_value = None  # project not found

    fake_response = SimpleNamespace(
        content='{"tasks": [], "summary": "Empty"}',
        total_tokens=10,
    )
    with patch("app.integration.llm_client.get_llm_client") as mock_get_llm:
        mock_client = AsyncMock()
        mock_client.chat.return_value = fake_response
        mock_get_llm.return_value = mock_client

        svc = TaskService(session)
        result = await svc.decompose_prd(
            TaskDecomposeRequest(prd_text="PRD", project_id="missing-proj")
        )

    assert result.summary == "Empty"


@pytest.mark.asyncio
async def test_decompose_prd_markdown_fence_stripping():
    """Lines 211-215: markdown code fences stripped from LLM output."""
    session = _make_session()
    fenced_content = '```json\n{"tasks": [{"title": "T", "description": "D", "priority": "low"}], "summary": "fenced"}\n```'
    fake_response = SimpleNamespace(content=fenced_content, total_tokens=30)

    with patch("app.integration.llm_client.get_llm_client") as mock_get_llm:
        mock_client = AsyncMock()
        mock_client.chat.return_value = fake_response
        mock_get_llm.return_value = mock_client

        svc = TaskService(session)
        result = await svc.decompose_prd(TaskDecomposeRequest(prd_text="PRD"))

    assert result.summary == "fenced"
    assert len(result.tasks) == 1


@pytest.mark.asyncio
async def test_decompose_prd_non_json_response():
    """Lines 219-225: non-JSON LLM response returns error summary."""
    session = _make_session()
    fake_response = SimpleNamespace(content="This is not JSON at all!", total_tokens=5)

    with patch("app.integration.llm_client.get_llm_client") as mock_get_llm:
        mock_client = AsyncMock()
        mock_client.chat.return_value = fake_response
        mock_get_llm.return_value = mock_client

        svc = TaskService(session)
        result = await svc.decompose_prd(TaskDecomposeRequest(prd_text="PRD"))

    assert result.tasks == []
    assert "格式错误" in result.summary


# ---------------------------------------------------------------------------
# batch_create (line 257)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_create_returns_created_count():
    """Lines 257-259: batch_create returns TaskBatchCreateResponse with correct count."""
    session = _make_session()
    task1 = _make_task(id="bt-1", title="Batch Task 1")
    task2 = _make_task(id="bt-2", title="Batch Task 2")
    # Each create_task call: flush + get (no template) + commit + execute (re-fetch)
    session.execute.side_effect = [
        _mock_result(scalar_one=task1),
        _mock_result(scalar_one=task2),
    ]

    svc = TaskService(session)
    request = TaskBatchCreateRequest(tasks=[
        BatchTaskItem(title="Batch Task 1"),
        BatchTaskItem(title="Batch Task 2"),
    ])
    result = await svc.batch_create(request)
    assert result.created == 2
    assert len(result.tasks) == 2
    assert result.tasks[0].id == "bt-1"
    assert result.tasks[1].id == "bt-2"


@pytest.mark.asyncio
async def test_batch_create_empty_list():
    """batch_create with empty list returns 0 created."""
    session = _make_session()
    svc = TaskService(session)
    result = await svc.batch_create(TaskBatchCreateRequest(tasks=[]))
    assert result.created == 0
    assert result.tasks == []


# ---------------------------------------------------------------------------
# retry_task (lines 282-312)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_task_not_found():
    """Lines 282-284: retry_task returns None when task not found."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    result = await svc.retry_task("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_retry_task_non_failed_status():
    """Lines 285-286: retry_task returns current response when task is not failed."""
    session = _make_session()
    task = _make_task(id="t1", status="running")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.retry_task("t1")
    assert result is not None
    assert result.status == "running"
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_retry_task_failed_resets_stages():
    """Lines 288-312: retry_task resets failed stages and commits."""
    session = _make_session()

    stage_failed = _make_stage(
        id="s1", status="failed", stage_name="coding", retry_count=0
    )
    stage_completed = _make_stage(
        id="s2", status="completed", stage_name="spec", tokens_used=500
    )
    task = _make_task(id="t1", status="failed", stages=[stage_failed, stage_completed])

    # First execute: for retry_task's initial load
    # Then _load_task_with_relations calls _load_task_with_relations_optional
    # which does another execute
    refreshed_task = _make_task(
        id="t1", status="pending", stages=[stage_failed, stage_completed]
    )
    session.execute.side_effect = [
        _mock_result(scalar_one_or_none=task),          # retry_task initial load
        _mock_result(scalar_one_or_none=refreshed_task), # _load_task_with_relations
    ]

    svc = TaskService(session)
    result = await svc.retry_task("t1")

    assert task.status == "pending"
    assert task.completed_at is None
    assert stage_failed.status == "pending"
    assert stage_failed.retry_count == 1
    assert stage_failed.error_message is None
    assert stage_completed.status == "completed"  # unchanged
    session.commit.assert_called_once()
    session.expire_all.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_retry_task_stage_at_max_retries_kept_failed():
    """Lines 298-303: stage at max retries is kept failed."""
    session = _make_session()

    # Stage already at default max_retries=3
    stage_at_limit = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=3)
    task = _make_task(id="t1", status="failed", stages=[stage_at_limit])

    refreshed_task = _make_task(id="t1", status="pending", stages=[stage_at_limit])
    session.execute.side_effect = [
        _mock_result(scalar_one_or_none=task),
        _mock_result(scalar_one_or_none=refreshed_task),
    ]

    svc = TaskService(session)
    await svc.retry_task("t1")

    # Stage should NOT be reset since it's at max retries
    assert stage_at_limit.status == "failed"
    assert stage_at_limit.retry_count == 3  # unchanged


# ---------------------------------------------------------------------------
# retry_from_stage (lines 329-354)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_from_stage_task_not_found():
    """Lines 329-330: retry_from_stage returns None when task not found."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    result = await svc.retry_from_stage("nonexistent", "stage-1")
    assert result is None


@pytest.mark.asyncio
async def test_retry_from_stage_task_non_failed_raises():
    """Lines 331-332: retry_from_stage raises ValueError when task not failed."""
    session = _make_session()
    task = _make_task(id="t1", status="running")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    with pytest.raises(ValueError, match="Task status must be failed"):
        await svc.retry_from_stage("t1", "stage-1")


@pytest.mark.asyncio
async def test_retry_from_stage_stage_not_found_raises():
    """Lines 334-336: retry_from_stage raises LookupError when stage not in task."""
    session = _make_session()
    task = _make_task(id="t1", status="failed", stages=[])
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    with pytest.raises(LookupError, match="Stage not found"):
        await svc.retry_from_stage("t1", "nonexistent-stage")


@pytest.mark.asyncio
async def test_retry_from_stage_stage_not_failed_raises():
    """Lines 337-338: retry_from_stage raises ValueError when stage not failed."""
    session = _make_session()
    stage = _make_stage(id="s1", status="pending")
    task = _make_task(id="t1", status="failed", stages=[stage])
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    with pytest.raises(ValueError, match="Stage status must be failed"):
        await svc.retry_from_stage("t1", "s1")


@pytest.mark.asyncio
async def test_retry_from_stage_stage_at_max_retries_raises():
    """Lines 340-344: retry_from_stage raises ValueError when stage at retry limit."""
    session = _make_session()
    stage = _make_stage(id="s1", status="failed", retry_count=3)
    task = _make_task(id="t1", status="failed", stages=[stage])
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    with pytest.raises(ValueError, match="retry limit"):
        await svc.retry_from_stage("t1", "s1")


@pytest.mark.asyncio
async def test_retry_from_stage_success():
    """Lines 346-354: retry_from_stage succeeds for a valid failed stage."""
    session = _make_session()
    stage = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=0)
    task = _make_task(id="t1", status="failed", stages=[stage])
    refreshed_task = _make_task(id="t1", status="pending", stages=[stage])

    session.execute.side_effect = [
        _mock_result(scalar_one_or_none=task),           # initial load
        _mock_result(scalar_one_or_none=refreshed_task), # _load_task_with_relations
    ]

    svc = TaskService(session)
    result = await svc.retry_from_stage("t1", "s1")

    assert task.status == "pending"
    assert task.completed_at is None
    assert stage.status == "pending"
    assert stage.retry_count == 1
    session.commit.assert_called_once()
    session.expire_all.assert_called_once()
    assert result is not None


# ---------------------------------------------------------------------------
# retry_batch (lines 370-406)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_batch_task_not_found():
    """Lines 370-372: retry_batch handles task not found."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    result = await svc.retry_batch(["nonexistent"])
    assert result.total == 1
    assert result.succeeded == 0
    assert result.failed == 1
    assert result.items[0].success is False
    assert "not found" in result.items[0].reason


@pytest.mark.asyncio
async def test_retry_batch_task_not_failed():
    """Lines 373-381: retry_batch skips non-failed tasks."""
    session = _make_session()
    task = _make_task(id="t1", status="running")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.retry_batch(["t1"])
    assert result.succeeded == 0
    assert result.failed == 1
    assert "not failed" in result.items[0].reason


@pytest.mark.asyncio
async def test_retry_batch_no_retryable_stage():
    """Lines 383-392: retry_batch handles no retryable failed stage."""
    session = _make_session()
    # Task is failed but has no failed stages
    task = _make_task(id="t1", status="failed", stages=[])
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.retry_batch(["t1"])
    assert result.succeeded == 0
    assert result.failed == 1
    assert "No failed stage" in result.items[0].reason


@pytest.mark.asyncio
async def test_retry_batch_all_stages_at_retry_limit():
    """Lines 474: all stages at retry limit → no retryable stage."""
    session = _make_session()
    stage = _make_stage(id="s1", status="failed", retry_count=3)
    task = _make_task(id="t1", status="failed", stages=[stage])
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc.retry_batch(["t1"])
    assert result.succeeded == 0
    assert result.failed == 1
    assert "retry limit" in result.items[0].reason


@pytest.mark.asyncio
async def test_retry_batch_success():
    """Lines 393-406: retry_batch successfully retries a task."""
    session = _make_session()
    stage = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=0)
    task = _make_task(id="t1", status="failed", stages=[stage])
    refreshed_task = _make_task(id="t1", status="pending", stages=[stage])

    session.execute.side_effect = [
        _mock_result(scalar_one_or_none=task),            # retry_batch _load_task_with_relations_optional
        _mock_result(scalar_one_or_none=task),            # retry_from_stage _load_task_with_relations_optional
        _mock_result(scalar_one_or_none=refreshed_task),  # _load_task_with_relations
    ]

    svc = TaskService(session)
    result = await svc.retry_batch(["t1"])
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.items[0].success is True
    assert result.items[0].task is not None


@pytest.mark.asyncio
async def test_retry_batch_retry_from_stage_raises_exception():
    """Lines 396-398: retry_batch handles exception from retry_from_stage."""
    session = _make_session()
    stage = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=0)
    task = _make_task(id="t1", status="failed", stages=[stage])

    session.execute.side_effect = [
        _mock_result(scalar_one_or_none=task),  # retry_batch load
        _mock_result(scalar_one_or_none=task),  # retry_from_stage load (returns failed task but status non-failed after mutate)
    ]

    # We'll monkeypatch retry_from_stage to raise ValueError
    svc = TaskService(session)

    async def raising_retry(*args, **kwargs):
        raise ValueError("Stage retry limit reached (3/3)")

    svc.retry_from_stage = raising_retry

    result = await svc.retry_batch(["t1"])
    assert result.succeeded == 0
    assert result.failed == 1
    assert "Stage retry limit" in result.items[0].reason


@pytest.mark.asyncio
async def test_retry_batch_retry_from_stage_returns_none():
    """Lines 400-404: retry_batch handles retry_from_stage returning None."""
    session = _make_session()
    stage = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=0)
    task = _make_task(id="t1", status="failed", stages=[stage])

    session.execute.return_value = _mock_result(scalar_one_or_none=task)

    svc = TaskService(session)

    async def none_retry(*args, **kwargs):
        return None

    svc.retry_from_stage = none_retry

    result = await svc.retry_batch(["t1"])
    assert result.succeeded == 0
    assert result.failed == 1
    assert "not found" in result.items[0].reason


@pytest.mark.asyncio
async def test_retry_batch_mixed_results():
    """retry_batch with mix of success and failure."""
    session = _make_session()

    # Task 1: not found
    # Task 2: succeeded
    stage = _make_stage(id="s2-1", status="failed", stage_name="spec", retry_count=0)
    task2 = _make_task(id="t2", status="failed", stages=[stage])
    refreshed_task2 = _make_task(id="t2", status="pending", stages=[stage])

    session.execute.side_effect = [
        _mock_result(scalar_one_or_none=None),             # t1 not found
        _mock_result(scalar_one_or_none=task2),            # t2 retry_batch load
        _mock_result(scalar_one_or_none=task2),            # t2 retry_from_stage load
        _mock_result(scalar_one_or_none=refreshed_task2),  # t2 _load_task_with_relations
    ]

    svc = TaskService(session)
    result = await svc.retry_batch(["t1", "t2"])
    assert result.total == 2
    assert result.succeeded == 1
    assert result.failed == 1


# ---------------------------------------------------------------------------
# _load_task_with_relations_optional (line 426)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_task_with_relations_optional_returns_none():
    """Line 426: _load_task_with_relations_optional returns None when not found."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    result = await svc._load_task_with_relations_optional("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_load_task_with_relations_optional_returns_task():
    """Line 426: _load_task_with_relations_optional returns task when found."""
    session = _make_session()
    task = _make_task(id="t1")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc._load_task_with_relations_optional("t1")
    assert result is task


# ---------------------------------------------------------------------------
# _load_task_with_relations (lines 431-433)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_task_with_relations_raises_when_not_found():
    """Lines 431-432: _load_task_with_relations raises LookupError when task missing."""
    session = _make_session()
    session.execute.return_value = _mock_result(scalar_one_or_none=None)
    svc = TaskService(session)
    with pytest.raises(LookupError, match="not found"):
        await svc._load_task_with_relations("nonexistent")


@pytest.mark.asyncio
async def test_load_task_with_relations_returns_task():
    """_load_task_with_relations returns task when found."""
    session = _make_session()
    task = _make_task(id="t1")
    session.execute.return_value = _mock_result(scalar_one_or_none=task)
    svc = TaskService(session)
    result = await svc._load_task_with_relations("t1")
    assert result is task


# ---------------------------------------------------------------------------
# _resolve_stage_max_retries (lines 445-446)
# ---------------------------------------------------------------------------


def test_resolve_stage_max_retries_default():
    """_resolve_stage_max_retries returns settings default when no template."""
    from app.config import settings
    session = _make_session()
    svc = TaskService(session)
    task = _make_task(template=None)
    stage = _make_stage(stage_name="coding")
    result = svc._resolve_stage_max_retries(task, stage)
    assert result == int(settings.STAGE_DEFAULT_MAX_RETRIES)


def test_resolve_stage_max_retries_from_template():
    """_resolve_stage_max_retries reads max_retries from template stage def."""
    session = _make_session()
    svc = TaskService(session)
    stages_json = json.dumps([
        {"name": "coding", "agent_role": "coding", "max_retries": 5},
    ])
    template = _make_template(stages_json=stages_json)
    task = _make_task(template=template)
    stage = _make_stage(stage_name="coding")
    result = svc._resolve_stage_max_retries(task, stage)
    assert result == 5


def test_resolve_stage_max_retries_json_decode_error():
    """Lines 445-446: _resolve_stage_max_retries falls back to default on JSON error."""
    from app.config import settings
    session = _make_session()
    svc = TaskService(session)
    template = _make_template(stages_json="NOT_VALID_JSON{{{{")
    task = _make_task(template=template)
    stage = _make_stage(stage_name="coding")
    result = svc._resolve_stage_max_retries(task, stage)
    assert result == int(settings.STAGE_DEFAULT_MAX_RETRIES)


def test_resolve_stage_max_retries_no_matching_stage_in_template():
    """_resolve_stage_max_retries returns default when stage name not in template."""
    from app.config import settings
    session = _make_session()
    svc = TaskService(session)
    stages_json = json.dumps([
        {"name": "spec", "agent_role": "spec", "max_retries": 2},
    ])
    template = _make_template(stages_json=stages_json)
    task = _make_task(template=template)
    stage = _make_stage(stage_name="coding")  # not in template
    result = svc._resolve_stage_max_retries(task, stage)
    assert result == int(settings.STAGE_DEFAULT_MAX_RETRIES)


# ---------------------------------------------------------------------------
# _select_retryable_failed_stage (lines 457-474)
# ---------------------------------------------------------------------------


def test_select_retryable_failed_stage_no_failed_stages():
    """Line 459: returns (None, reason) when no failed stages."""
    session = _make_session()
    svc = TaskService(session)
    task = _make_task(stages=[
        _make_stage(status="completed"),
        _make_stage(status="pending"),
    ])
    result, reason = svc._select_retryable_failed_stage(task)
    assert result is None
    assert "No failed stage" in reason


def test_select_retryable_failed_stage_picks_first_retryable():
    """_select_retryable_failed_stage picks first stage with retry remaining."""
    session = _make_session()
    svc = TaskService(session)
    stage1 = _make_stage(id="s1", status="failed", stage_name="spec", retry_count=0)
    stage2 = _make_stage(id="s2", status="failed", stage_name="coding", retry_count=0)
    task = _make_task(stages=[stage1, stage2])
    result, reason = svc._select_retryable_failed_stage(task)
    assert result is not None
    assert reason is None


def test_select_retryable_failed_stage_all_at_limit():
    """Line 474: returns (None, reason) when all failed stages exhausted."""
    session = _make_session()
    svc = TaskService(session)
    stage = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=3)
    task = _make_task(stages=[stage])
    result, reason = svc._select_retryable_failed_stage(task)
    assert result is None
    assert "retry limit" in reason


def test_select_retryable_failed_stage_uses_template_order():
    """Lines 461-467: stages sorted by template order when template present."""
    session = _make_session()
    svc = TaskService(session)
    stages_json = json.dumps([
        {"name": "spec", "agent_role": "spec", "order": 1},
        {"name": "coding", "agent_role": "coding", "order": 2},
    ])
    template = _make_template(stages_json=stages_json)
    # coding listed first in stages but has higher template order
    stage_coding = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=0)
    stage_spec = _make_stage(id="s2", status="failed", stage_name="spec", retry_count=0)
    task = _make_task(stages=[stage_coding, stage_spec], template=template)
    result, reason = svc._select_retryable_failed_stage(task)
    assert result is not None
    assert result.stage_name == "spec"  # spec has lower order


def test_select_retryable_failed_stage_template_json_error_falls_back():
    """Lines 466-467: invalid template JSON → empty order_map, still selects stage."""
    session = _make_session()
    svc = TaskService(session)
    template = _make_template(stages_json="INVALID_JSON_{{")
    stage = _make_stage(id="s1", status="failed", stage_name="coding", retry_count=0)
    task = _make_task(stages=[stage], template=template)
    result, reason = svc._select_retryable_failed_stage(task)
    # Even with JSON error, fallback order_map={} and stage is still retryable
    assert result is not None
    assert reason is None


# ---------------------------------------------------------------------------
# _reset_stage_for_retry (sync helper)
# ---------------------------------------------------------------------------


def test_reset_stage_for_retry_increments_count():
    """_reset_stage_for_retry resets fields and increments retry_count."""
    session = _make_session()
    svc = TaskService(session)
    stage = _make_stage(
        status="failed",
        retry_count=2,
        error_message="oops",
        failure_category="timeout",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_seconds=30.0,
        tokens_used=500,
        output_summary="old output",
        output_structured={"key": "val"},
    )
    svc._reset_stage_for_retry(stage, increment_retry=True)
    assert stage.status == "pending"
    assert stage.retry_count == 3
    assert stage.error_message is None
    assert stage.failure_category is None
    assert stage.started_at is None
    assert stage.completed_at is None
    assert stage.duration_seconds is None
    assert stage.tokens_used == 0
    assert stage.output_summary is None
    assert stage.output_structured is None


def test_reset_stage_for_retry_no_increment():
    """_reset_stage_for_retry with increment_retry=False does not change count."""
    session = _make_session()
    svc = TaskService(session)
    stage = _make_stage(status="failed", retry_count=1)
    svc._reset_stage_for_retry(stage, increment_retry=False)
    assert stage.retry_count == 1


# ---------------------------------------------------------------------------
# _recalculate_task_usage (sync helper)
# ---------------------------------------------------------------------------


def test_recalculate_task_usage():
    """_recalculate_task_usage sums tokens from completed stages only."""
    session = _make_session()
    svc = TaskService(session)
    s1 = _make_stage(status="completed", tokens_used=1000)
    s2 = _make_stage(status="completed", tokens_used=2000)
    s3 = _make_stage(status="failed", tokens_used=500)  # excluded
    task = _make_task(stages=[s1, s2, s3])
    svc._recalculate_task_usage(task)
    assert task.total_tokens == 3000


# ---------------------------------------------------------------------------
# _task_to_response (static method)
# ---------------------------------------------------------------------------


def test_task_to_response_with_template_and_project():
    """_task_to_response maps all fields including template/project names."""
    template = SimpleNamespace(display_name="My Template")
    project = SimpleNamespace(display_name="My Project")
    task = _make_task(
        id="t1",
        title="Test",
        template=template,
        project=project,
        template_id="tmpl-1",
        project_id="proj-1",
    )
    result = TaskService._task_to_response(task)
    assert result.id == "t1"
    assert result.template_name == "My Template"
    assert result.project_name == "My Project"


def test_task_to_response_no_template_no_project():
    """_task_to_response handles None template/project."""
    task = _make_task(id="t2", template=None, project=None)
    result = TaskService._task_to_response(task)
    assert result.template_name is None
    assert result.project_name is None
