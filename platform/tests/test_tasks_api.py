"""Integration tests for the Tasks API endpoints."""
import json

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
