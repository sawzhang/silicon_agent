"""Integration tests for the Tasks API endpoints."""
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.project import ProjectModel
from app.models.task import TaskModel, TaskStageModel
from app.models.template import TaskTemplateModel


# ── Fixtures ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_template_with_stages():
    """Seed a template whose stages JSON will auto-generate TaskStageModels."""
    stages = json.dumps([
        {"name": "design", "agent_role": "design"},
        {"name": "coding", "agent_role": "coding"},
        {"name": "test", "agent_role": "test"},
    ])
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            id="tt-tmpl-stages",
            name="tt_stages_template",
            display_name="TT Stages Template",
            description="Template with three stages",
            stages=stages,
            gates="[]",
        )
        session.add(tmpl)
        await session.commit()

    yield "tt-tmpl-stages"

    async with async_session_factory() as session:
        # Clean up stages, tasks referencing this template, then the template
        for cls in [TaskStageModel, TaskModel]:
            result = await session.execute(select(cls))
            for obj in result.scalars().all():
                if obj.id.startswith("tt-"):
                    await session.delete(obj)
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.id == "tt-tmpl-stages")
        )
        tmpl = result.scalar_one_or_none()
        if tmpl:
            await session.delete(tmpl)
        await session.commit()


@pytest_asyncio.fixture
async def seed_multiple_tasks():
    """Seed three tasks with different statuses for list/filter tests."""
    async with async_session_factory() as session:
        for i, status in enumerate(["pending", "running", "completed"], start=1):
            t = TaskModel(
                id=f"tt-list-{i}",
                title=f"List Task {i}",
                status=status,
            )
            session.add(t)
        await session.commit()

    yield

    async with async_session_factory() as session:
        result = await session.execute(select(TaskModel))
        for obj in result.scalars().all():
            if obj.id.startswith("tt-list-"):
                await session.delete(obj)
        await session.commit()


@pytest_asyncio.fixture
async def seed_task_with_stages():
    """Seed a single task that already has stages attached."""
    async with async_session_factory() as session:
        t = TaskModel(id="tt-staged", title="Staged Task", status="running")
        session.add(t)
        for idx, name in enumerate(["design", "coding"]):
            s = TaskStageModel(
                id=f"tt-stage-{idx}",
                task_id="tt-staged",
                stage_name=name,
                agent_role=name,
                status="pending",
            )
            session.add(s)
        await session.commit()

    yield "tt-staged"

    async with async_session_factory() as session:
        for cls in [TaskStageModel, TaskModel]:
            result = await session.execute(select(cls))
            for obj in result.scalars().all():
                if obj.id.startswith("tt-stag"):
                    await session.delete(obj)
        await session.commit()


@pytest_asyncio.fixture
async def seed_tasks_for_query_filters():
    async with async_session_factory() as session:
        project_a = ProjectModel(id='tt-proj-a', name='tt-proj-a', display_name='TT Project A')
        project_b = ProjectModel(id='tt-proj-b', name='tt-proj-b', display_name='TT Project B')
        session.add(project_a)
        session.add(project_b)
        session.add_all(
            [
                TaskModel(id='tt-filter-1', title='Alpha Login Task', status='pending', project_id='tt-proj-a'),
                TaskModel(id='tt-filter-2', title='Alpha Payment Task', status='pending', project_id='tt-proj-b'),
                TaskModel(id='tt-filter-3', title='Beta Cache Task', status='pending', project_id='tt-proj-a'),
            ]
        )
        await session.commit()

    yield

    async with async_session_factory() as session:
        stage_result = await session.execute(
            select(TaskStageModel).where(TaskStageModel.task_id.in_(['tt-filter-1', 'tt-filter-2', 'tt-filter-3']))
        )
        for stage in stage_result.scalars().all():
            await session.delete(stage)

        task_result = await session.execute(select(TaskModel).where(TaskModel.id.like('tt-filter-%')))
        for task in task_result.scalars().all():
            await session.delete(task)

        project_result = await session.execute(select(ProjectModel).where(ProjectModel.id.in_(['tt-proj-a', 'tt-proj-b'])))
        for project in project_result.scalars().all():
            await session.delete(project)

        await session.commit()


# ── Create Task Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_task(client):
    """POST /api/v1/tasks creates a task and returns 201 with expected fields."""
    resp = await client.post("/api/v1/tasks", json={
        "title": "TT Create Test",
        "description": "A simple task",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "TT Create Test"
    assert data["description"] == "A simple task"
    assert data["status"] == "pending"
    assert data["target_branch"] == f"silicon_agent/{data['id'].rsplit('-', 1)[-1]}"
    assert "id" in data
    assert "created_at" in data
    assert data["stages"] == []

    # Cleanup
    task_id = data["id"]
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskModel).where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            await session.delete(task)
            await session.commit()


@pytest.mark.asyncio
async def test_create_task_rejects_manual_target_branch(client):
    """POST /api/v1/tasks rejects manually supplied target_branch values."""
    resp = await client.post("/api/v1/tasks", json={
        "title": "TT Create Invalid Branch",
        "target_branch": "task/manual-branch",
    })
    assert resp.status_code == 422
    data = resp.json()
    assert "target_branch" in json.dumps(data, ensure_ascii=False)
    assert "自动创建" in json.dumps(data, ensure_ascii=False)


@pytest.mark.asyncio
async def test_create_task_with_template(client, seed_template_with_stages):
    """POST /api/v1/tasks with template_id auto-generates stages from template."""
    template_id = seed_template_with_stages
    resp = await client.post("/api/v1/tasks", json={
        "title": "TT Template Task",
        "template_id": template_id,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["template_id"] == template_id
    assert len(data["stages"]) == 3
    stage_names = [s["stage_name"] for s in data["stages"]]
    assert "design" in stage_names
    assert "coding" in stage_names
    assert "test" in stage_names
    for stage in data["stages"]:
        assert stage["status"] == "pending"


@pytest.mark.asyncio
async def test_batch_create_task_rejects_manual_target_branch(client):
    """POST /api/v1/tasks/batch rejects manually supplied target_branch values."""
    resp = await client.post("/api/v1/tasks/batch", json={
        "tasks": [
            {
                "title": "TT Batch Invalid Branch",
                "target_branch": "task/manual-branch",
            }
        ]
    })
    assert resp.status_code == 422
    data = resp.json()
    assert "target_branch" in json.dumps(data, ensure_ascii=False)
    assert "自动创建" in json.dumps(data, ensure_ascii=False)


# ── Get Task Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task(client):
    """GET /api/v1/tasks/{id} returns the task with correct fields."""
    # Create a task first
    create_resp = await client.post("/api/v1/tasks", json={
        "title": "TT Get Test",
        "description": "Get me",
    })
    task_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task_id
    assert data["title"] == "TT Get Test"
    assert data["description"] == "Get me"
    assert data["status"] == "pending"

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskModel).where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            await session.delete(task)
            await session.commit()


@pytest.mark.asyncio
async def test_get_task_404(client):
    """GET /api/v1/tasks/{id} returns 404 for nonexistent task."""
    resp = await client.get("/api/v1/tasks/tt-nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_clone_task_creates_fresh_pending_copy(client, seed_template_with_stages):
    """POST /api/v1/tasks/{id}/clone creates a new task without inheriting runtime state."""
    template_id = seed_template_with_stages
    create_resp = await client.post("/api/v1/tasks", json={
        "title": "TT Clone Source",
        "description": "Clone this task",
        "template_id": template_id,
        "jira_id": "TT-123",
        "project_id": None,
        "yunxiao_task_id": "YX-123",
    })
    assert create_resp.status_code == 201
    source = create_resp.json()

    async with async_session_factory() as session:
        result = await session.execute(select(TaskModel).where(TaskModel.id == source["id"]))
        task = result.scalar_one()
        task.status = "failed"
        task.branch_name = "feature/original"
        task.pr_url = "https://example.com/pr/123"

        stage_result = await session.execute(
            select(TaskStageModel).where(TaskStageModel.task_id == source["id"])
        )
        stages = stage_result.scalars().all()
        stages[0].status = "completed"
        stages[1].status = "failed"
        stages[1].retry_count = 2
        stages[1].error_message = "compile failed"
        await session.commit()

    clone_resp = await client.post(f"/api/v1/tasks/{source['id']}/clone")
    assert clone_resp.status_code == 201
    cloned = clone_resp.json()

    assert cloned["id"] != source["id"]
    assert cloned["title"] == source["title"]
    assert cloned["description"] == source["description"]
    assert cloned["jira_id"] == source["jira_id"]
    assert cloned["template_id"] == source["template_id"]
    assert cloned["yunxiao_task_id"] == source["yunxiao_task_id"]
    assert cloned["status"] == "pending"
    assert cloned["branch_name"] is None
    assert cloned["pr_url"] is None
    assert cloned["target_branch"] == f"silicon_agent/{cloned['id'].rsplit('-', 1)[-1]}"
    assert len(cloned["stages"]) == 3
    for stage in cloned["stages"]:
        assert stage["status"] == "pending"
        assert stage["retry_count"] == 0
        assert stage["error_message"] is None

    async with async_session_factory() as session:
        stage_result = await session.execute(
            select(TaskStageModel).where(TaskStageModel.task_id.in_([source["id"], cloned["id"]]))
        )
        for stage in stage_result.scalars().all():
            await session.delete(stage)

        task_result = await session.execute(
            select(TaskModel).where(TaskModel.id.in_([source["id"], cloned["id"]]))
        )
        for task in task_result.scalars().all():
            await session.delete(task)
        await session.commit()


@pytest.mark.asyncio
async def test_clone_task_404(client):
    """POST /api/v1/tasks/{id}/clone returns 404 for nonexistent task."""
    resp = await client.post("/api/v1/tasks/tt-nonexistent-id/clone")
    assert resp.status_code == 404


# ── List Tasks Tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tasks_pagination(client, seed_multiple_tasks):
    """GET /api/v1/tasks with pagination returns correct page and total."""
    resp = await client.get("/api/v1/tasks", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2
    assert data["total"] >= 3
    assert data["page"] == 1
    assert data["page_size"] == 2


@pytest.mark.asyncio
async def test_list_tasks_status_filter(client, seed_multiple_tasks):
    """GET /api/v1/tasks?status=pending returns only pending tasks."""
    resp = await client.get("/api/v1/tasks", params={"status": "pending"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["status"] == "pending"
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_tasks_title_filter(client, seed_tasks_for_query_filters):
    resp = await client.get('/api/v1/tasks', params={'title': 'alpha', 'page_size': 200})
    assert resp.status_code == 200
    data = resp.json()

    assert data['total'] >= 2
    assert data['items']
    for item in data['items']:
        assert 'alpha' in item['title'].lower()


@pytest.mark.asyncio
async def test_list_tasks_project_filter(client, seed_tasks_for_query_filters):
    resp = await client.get('/api/v1/tasks', params={'project_id': 'tt-proj-a', 'page_size': 200})
    assert resp.status_code == 200
    data = resp.json()

    assert data['items']
    for item in data['items']:
        assert item['project_id'] == 'tt-proj-a'


# ── Get Stages Tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_stages(client, seed_task_with_stages):
    """GET /api/v1/tasks/{id}/stages returns all stages for the task."""
    task_id = seed_task_with_stages
    resp = await client.get(f"/api/v1/tasks/{task_id}/stages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = [s["stage_name"] for s in data]
    assert "design" in names
    assert "coding" in names


# ── Cancel Task Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_task(client):
    """POST /api/v1/tasks/{id}/cancel cancels a pending task."""
    create_resp = await client.post("/api/v1/tasks", json={
        "title": "TT Cancel Me",
    })
    task_id = create_resp.json()["id"]

    resp = await client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["completed_at"] is not None

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskModel).where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            await session.delete(task)
            await session.commit()


@pytest.mark.asyncio
async def test_cancel_completed_task(client):
    """Cancelling a completed task leaves the status unchanged."""
    # Seed completed task directly
    async with async_session_factory() as session:
        t = TaskModel(id="tt-cancel-done", title="Already Done", status="completed")
        session.add(t)
        await session.commit()

    resp = await client.post("/api/v1/tasks/tt-cancel-done/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskModel).where(TaskModel.id == "tt-cancel-done")
        )
        task = result.scalar_one_or_none()
        if task:
            await session.delete(task)
            await session.commit()


@pytest.mark.asyncio
async def test_cancel_task_404(client):
    """POST /api/v1/tasks/nonexistent/cancel returns 404."""
    resp = await client.post("/api/v1/tasks/tt-nonexistent/cancel")
    assert resp.status_code == 404


# ── Retry Task Tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_completed_task(client):
    """Retrying a running (non-failed) task returns it unchanged."""
    async with async_session_factory() as session:
        t = TaskModel(id="tt-retry-run", title="Running Task", status="running")
        session.add(t)
        await session.commit()

    resp = await client.post("/api/v1/tasks/tt-retry-run/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskModel).where(TaskModel.id == "tt-retry-run")
        )
        task = result.scalar_one_or_none()
        if task:
            await session.delete(task)
            await session.commit()


# ── Batch Create Tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_create(client):
    """POST /api/v1/tasks/batch creates multiple tasks at once."""
    resp = await client.post("/api/v1/tasks/batch", json={
        "tasks": [
            {"title": "TT Batch 1", "description": "First batch task"},
            {"title": "TT Batch 2", "description": "Second batch task"},
        ]
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["created"] == 2
    assert len(data["tasks"]) == 2
    titles = [t["title"] for t in data["tasks"]]
    assert "TT Batch 1" in titles
    assert "TT Batch 2" in titles
    for t in data["tasks"]:
        assert t["status"] == "pending"
        assert t["target_branch"] == f"silicon_agent/{t['id'].rsplit('-', 1)[-1]}"

    # Cleanup
    task_ids = [t["id"] for t in data["tasks"]]
    async with async_session_factory() as session:
        for tid in task_ids:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == tid)
            )
            task = result.scalar_one_or_none()
            if task:
                await session.delete(task)
        await session.commit()


@pytest_asyncio.fixture
async def seed_retry_enhanced_tasks():
    """Seed tasks/stages for retry-from-stage and retry-batch tests."""
    stages = json.dumps([
        {"name": "parse", "agent_role": "orchestrator"},
        {"name": "coding", "agent_role": "coding", "max_retries": 2},
        {"name": "test", "agent_role": "test"},
    ])
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            id="tt-retry-enhanced-tmpl",
            name="tt_retry_enhanced_tmpl",
            display_name="TT Retry Enhanced Template",
            description="Retry enhanced template",
            stages=stages,
            gates="[]",
        )
        session.add(tmpl)

        failed_task = TaskModel(
            id="tt-retry-enhanced-failed",
            title="Retry Enhanced Failed",
            status="failed",
            template_id="tt-retry-enhanced-tmpl",
        )
        running_task = TaskModel(
            id="tt-retry-enhanced-running",
            title="Retry Enhanced Running",
            status="running",
            template_id="tt-retry-enhanced-tmpl",
        )
        session.add(failed_task)
        session.add(running_task)

        session.add_all(
            [
                TaskStageModel(
                    id="tt-retry-enhanced-stage-parse",
                    task_id="tt-retry-enhanced-failed",
                    stage_name="parse",
                    agent_role="orchestrator",
                    status="completed",
                    tokens_used=30,
                ),
                TaskStageModel(
                    id="tt-retry-enhanced-stage-coding",
                    task_id="tt-retry-enhanced-failed",
                    stage_name="coding",
                    agent_role="coding",
                    status="failed",
                    retry_count=1,
                    error_message="build failed",
                    output_summary="failed output",
                    tokens_used=50,
                ),
                TaskStageModel(
                    id="tt-retry-enhanced-stage-test",
                    task_id="tt-retry-enhanced-failed",
                    stage_name="test",
                    agent_role="test",
                    status="failed",
                    retry_count=0,
                    error_message="test failed",
                    output_summary="test failed output",
                ),
            ]
        )
        await session.commit()

    yield

    async with async_session_factory() as session:
        for stage_id in [
            "tt-retry-enhanced-stage-parse",
            "tt-retry-enhanced-stage-coding",
            "tt-retry-enhanced-stage-test",
        ]:
            stage = await session.get(TaskStageModel, stage_id)
            if stage:
                await session.delete(stage)

        for task_id in ["tt-retry-enhanced-failed", "tt-retry-enhanced-running"]:
            task = await session.get(TaskModel, task_id)
            if task:
                await session.delete(task)

        tmpl = await session.get(TaskTemplateModel, "tt-retry-enhanced-tmpl")
        if tmpl:
            await session.delete(tmpl)
        await session.commit()


@pytest.mark.asyncio
async def test_retry_from_stage_success(client, seed_retry_enhanced_tasks):
    """Retrying from a failed stage resets that stage and task status."""
    resp = await client.post(
        "/api/v1/tasks/tt-retry-enhanced-failed/retry-from-stage",
        json={"stage_id": "tt-retry-enhanced-stage-coding"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"

    coding = next(s for s in data["stages"] if s["id"] == "tt-retry-enhanced-stage-coding")
    assert coding["status"] == "pending"
    assert coding["retry_count"] == 2
    assert coding["error_message"] is None
    assert coding["output_summary"] is None


@pytest.mark.asyncio
async def test_retry_from_stage_rejects_non_failed_stage(client, seed_retry_enhanced_tasks):
    """retry-from-stage returns 400 when target stage is not failed."""
    resp = await client.post(
        "/api/v1/tasks/tt-retry-enhanced-failed/retry-from-stage",
        json={"stage_id": "tt-retry-enhanced-stage-parse"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_retry_from_stage_stage_not_found(client, seed_retry_enhanced_tasks):
    """retry-from-stage returns 404 when stage id does not belong to task."""
    resp = await client.post(
        "/api/v1/tasks/tt-retry-enhanced-failed/retry-from-stage",
        json={"stage_id": "missing-stage"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_batch_mixed_results(client, seed_retry_enhanced_tasks):
    """Batch retry reports mixed outcomes and only resets one failed stage."""
    resp = await client.post(
        "/api/v1/tasks/retry-batch",
        json={
            "task_ids": [
                "tt-retry-enhanced-failed",
                "tt-retry-enhanced-running",
                "tt-retry-enhanced-missing",
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["succeeded"] == 1
    assert data["failed"] == 2

    by_id = {item["task_id"]: item for item in data["items"]}
    assert by_id["tt-retry-enhanced-failed"]["success"] is True
    assert by_id["tt-retry-enhanced-failed"]["task"]["status"] == "pending"
    stages = {stage["id"]: stage for stage in by_id["tt-retry-enhanced-failed"]["task"]["stages"]}
    # Retry from failed node should reset only one stage (earliest by template order: coding).
    assert stages["tt-retry-enhanced-stage-coding"]["status"] == "pending"
    assert stages["tt-retry-enhanced-stage-test"]["status"] == "failed"
    assert by_id["tt-retry-enhanced-running"]["success"] is False
    assert by_id["tt-retry-enhanced-missing"]["success"] is False


# ── New: decompose_prd ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decompose_prd_returns_tasks(client, monkeypatch):
    """POST /tasks/decompose with mocked LLM returns parsed task list."""
    import app.services.task_service as task_service_mod
    from app.integration.llm_client import LLMResponse

    fake_response = LLMResponse(
        content='{"tasks": [{"title": "Setup DB", "description": "Init schema", "priority": "high"}], '
                '"summary": "1 task identified"}',
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        model="test-model",
    )

    fake_client = type("FakeLLM", (), {"chat": AsyncMock(return_value=fake_response)})()
    monkeypatch.setattr(task_service_mod, "get_llm_client" if hasattr(task_service_mod, "get_llm_client") else "_", lambda: fake_client, raising=False)

    # Patch at the import level used inside decompose_prd
    import app.integration.llm_client as llm_mod
    original = llm_mod.get_llm_client
    llm_mod.get_llm_client = lambda: fake_client

    try:
        resp = await client.post("/api/v1/tasks/decompose", json={
            "prd_text": "Build a login feature with JWT auth",
        })
    finally:
        llm_mod.get_llm_client = original

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["title"] == "Setup DB"
    assert data["tasks"][0]["priority"] == "high"
    assert data["summary"] == "1 task identified"
    assert data["tokens_used"] == 30


@pytest.mark.asyncio
async def test_decompose_prd_handles_invalid_json_from_llm(client):
    """POST /tasks/decompose when LLM returns non-JSON → returns empty tasks with error summary."""
    import app.integration.llm_client as llm_mod
    from app.integration.llm_client import LLMResponse

    fake_response = LLMResponse(
        content="Sorry, I cannot help with that.",
        input_tokens=5,
        output_tokens=10,
        total_tokens=15,
        model="test-model",
    )
    fake_client = type("FakeLLM", (), {"chat": AsyncMock(return_value=fake_response)})()
    original = llm_mod.get_llm_client
    llm_mod.get_llm_client = lambda: fake_client

    try:
        resp = await client.post("/api/v1/tasks/decompose", json={
            "prd_text": "Some PRD text",
        })
    finally:
        llm_mod.get_llm_client = original

    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == []
    assert "错误" in data["summary"] or "error" in data["summary"].lower() or "格式" in data["summary"]


# ── New: retry failed task ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_failed_task_resets_to_pending(client):
    """POST /tasks/{id}/retry on a failed task resets it to pending."""
    async with async_session_factory() as session:
        task = TaskModel(id="tt-retry-failed-1", title="Failed Task", status="failed")
        session.add(task)
        stage = TaskStageModel(
            id="tt-retry-failed-stage-1",
            task_id="tt-retry-failed-1",
            stage_name="coding",
            agent_role="coding",
            status="failed",
            retry_count=0,
            error_message="build error",
        )
        session.add(stage)
        await session.commit()

    resp = await client.post("/api/v1/tasks/tt-retry-failed-1/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"

    coding_stage = next((s for s in data["stages"] if s["id"] == "tt-retry-failed-stage-1"), None)
    assert coding_stage is not None
    assert coding_stage["status"] == "pending"
    assert coding_stage["retry_count"] == 1
    assert coding_stage["error_message"] is None

    # Cleanup
    async with async_session_factory() as session:
        for cls, obj_id in [(TaskStageModel, "tt-retry-failed-stage-1"), (TaskModel, "tt-retry-failed-1")]:
            obj = await session.get(cls, obj_id)
            if obj:
                await session.delete(obj)
        await session.commit()


# ── New: _reset_stage_for_retry via retry-from-stage ──────────────────────


@pytest.mark.asyncio
async def test_reset_stage_for_retry_clears_fields(client, seed_retry_enhanced_tasks):
    """retry-from-stage triggers _reset_stage_for_retry: output cleared, retry_count incremented."""
    resp = await client.post(
        "/api/v1/tasks/tt-retry-enhanced-failed/retry-from-stage",
        json={"stage_id": "tt-retry-enhanced-stage-coding"},
    )
    assert resp.status_code == 200
    data = resp.json()

    coding = next(s for s in data["stages"] if s["id"] == "tt-retry-enhanced-stage-coding")
    # Fields that _reset_stage_for_retry clears
    assert coding["status"] == "pending"
    assert coding["error_message"] is None
    assert coding["output_summary"] is None
    assert coding["retry_count"] == 2  # was 1, incremented


# ── New: batch retry edge cases ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_batch_empty_list(client):
    """Batch retry with empty task_ids list returns empty result without error."""
    resp = await client.post("/api/v1/tasks/retry-batch", json={"task_ids": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["succeeded"] == 0
    assert data["failed"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_retry_batch_all_nonexistent(client):
    """Batch retry where all task_ids don't exist → all failed."""
    resp = await client.post("/api/v1/tasks/retry-batch", json={
        "task_ids": ["nonexistent-1", "nonexistent-2"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["succeeded"] == 0
    assert data["failed"] == 2
    for item in data["items"]:
        assert item["success"] is False
        assert "not found" in item["reason"].lower()


# ── New: _recalculate_task_usage via retry ─────────────────────────────────


@pytest.mark.asyncio
async def test_recalculate_task_usage_after_retry(client):
    """After retry, task.total_tokens == sum of only completed stages."""
    task_id = "tt-usage-task-1"
    async with async_session_factory() as session:
        session.add(TaskModel(
            id=task_id, title="Usage Task", status="failed",
            total_tokens=200, total_cost_rmb=0.2,
        ))
        session.add_all([
            TaskStageModel(
                id="tt-usage-stage-done",
                task_id=task_id, stage_name="parse", agent_role="orchestrator",
                status="completed", tokens_used=80,
            ),
            TaskStageModel(
                id="tt-usage-stage-fail",
                task_id=task_id, stage_name="coding", agent_role="coding",
                status="failed", tokens_used=120, retry_count=0,
                error_message="compile error",
            ),
        ])
        await session.commit()

    resp = await client.post(f"/api/v1/tasks/{task_id}/retry")
    assert resp.status_code == 200
    data = resp.json()

    # After retry: failed stage tokens_used is reset to 0; only completed stage counts
    assert data["total_tokens"] == 80

    # Cleanup
    async with async_session_factory() as session:
        for cls, oid in [
            (TaskStageModel, "tt-usage-stage-done"),
            (TaskStageModel, "tt-usage-stage-fail"),
            (TaskModel, task_id),
        ]:
            obj = await session.get(cls, oid)
            if obj:
                await session.delete(obj)
        await session.commit()


# ── New: NULL stage fields → schema coercion regression ────────────────────


def test_list_tasks_with_null_stage_fields():
    """TaskStageResponse coerces NULL int columns to 0 (legacy rows defense).

    In production, ALTER TABLE ADD COLUMN on existing rows can leave int
    columns as NULL. The fresh test DB has NOT NULL constraints so we cannot
    create NULL rows via SQL; instead we validate the Pydantic schema layer
    directly, which is the actual defense that prevents API 500.
    """
    from app.schemas.task import TaskStageResponse

    # Simulate ORM object with None int fields (as returned by legacy rows)
    stage = TaskStageResponse.model_validate({
        "id": "fake-stage",
        "task_id": "fake-task",
        "stage_name": "coding",
        "agent_role": "coding",
        "status": "pending",
        "tokens_used": None,
        "turns_used": None,
        "self_fix_count": None,
        "retry_count": None,
    })
    assert stage.tokens_used == 0
    assert stage.turns_used == 0
    assert stage.self_fix_count == 0
    assert stage.retry_count == 0


def test_task_detail_response_null_tokens():
    """TaskDetailResponse coerces NULL total_tokens and total_cost_rmb to defaults."""
    from app.schemas.task import TaskDetailResponse
    from datetime import datetime

    task = TaskDetailResponse.model_validate({
        "id": "fake-task",
        "title": "Test",
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "total_tokens": None,
        "total_cost_rmb": None,
    })
    assert task.total_tokens == 0
    assert task.total_cost_rmb == 0.0
