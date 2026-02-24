"""Tests for ROI Dashboard and Developer Cockpit endpoints."""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.task import TaskModel, TaskStageModel
from app.models.gate import HumanGateModel
from app.models.template import TaskTemplateModel


@pytest_asyncio.fixture
async def seed_tasks():
    """Seed completed, running, and failed tasks for testing."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        # Completed task
        t1 = TaskModel(
            id="roi-task-1",
            title="Login Feature",
            status="completed",
            total_tokens=50000,
            total_cost_rmb=0.5,
            created_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=1),
        )
        session.add(t1)

        # Stage for completed task
        s1 = TaskStageModel(
            id="stage-roi-1",
            task_id="roi-task-1",
            stage_name="coding",
            agent_role="coding",
            status="completed",
            tokens_used=30000,
            duration_seconds=120.0,
        )
        session.add(s1)

        s2 = TaskStageModel(
            id="stage-roi-2",
            task_id="roi-task-1",
            stage_name="test",
            agent_role="test",
            status="completed",
            tokens_used=20000,
            duration_seconds=60.0,
        )
        session.add(s2)

        # Running task
        t2 = TaskModel(
            id="cockpit-task-running",
            title="API Refactor",
            status="running",
            total_tokens=15000,
            total_cost_rmb=0.15,
            created_at=now - timedelta(minutes=30),
        )
        session.add(t2)

        s3 = TaskStageModel(
            id="stage-cockpit-1",
            task_id="cockpit-task-running",
            stage_name="coding",
            agent_role="coding",
            status="running",
            tokens_used=15000,
            duration_seconds=0.0,
        )
        session.add(s3)

        # Failed task (today)
        t3 = TaskModel(
            id="cockpit-task-failed",
            title="Broken Build",
            status="failed",
            total_tokens=8000,
            total_cost_rmb=0.08,
            created_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=30),
        )
        session.add(t3)

        s4 = TaskStageModel(
            id="stage-cockpit-2",
            task_id="cockpit-task-failed",
            stage_name="test",
            agent_role="test",
            status="failed",
            tokens_used=8000,
            duration_seconds=45.0,
            error_message="Test suite failed with 3 errors",
        )
        session.add(s4)

        # Pending gate
        g1 = HumanGateModel(
            id="gate-cockpit-1",
            gate_type="review",
            task_id="cockpit-task-running",
            agent_role="review",
            status="pending",
            content={"stage": "review", "summary": "Please review the API refactor"},
        )
        session.add(g1)

        await session.commit()

    yield

    # Cleanup
    async with async_session_factory() as session:
        for model_cls in [TaskStageModel, HumanGateModel, TaskModel, TaskTemplateModel]:
            result = await session.execute(select(model_cls))
            for obj in result.scalars().all():
                if obj.id.startswith(("roi-", "cockpit-", "stage-roi-", "stage-cockpit-", "gate-cockpit-", "tmpl-")):
                    await session.delete(obj)
        await session.commit()


# ── ROI Endpoint Tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_roi_empty(client):
    """ROI endpoint returns valid response with no data."""
    resp = await client.get("/api/v1/kpi/roi", params={"days": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tasks_completed"] >= 0
    assert data["roi_ratio"] >= 0
    assert "by_role" in data
    assert "recent_tasks" in data
    assert "benchmark_hours_per_task" in data
    assert "benchmark_hourly_rate" in data


@pytest.mark.asyncio
async def test_roi_with_data(client, seed_tasks):
    """ROI endpoint returns correct summary with seeded tasks."""
    resp = await client.get("/api/v1/kpi/roi", params={"days": 30})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_tasks_completed"] >= 1
    assert data["total_agent_cost_rmb"] > 0
    assert data["total_estimated_manual_rmb"] > 0
    assert data["total_savings_rmb"] > 0
    assert data["roi_ratio"] > 0
    assert data["benchmark_hours_per_task"] == 8.0
    assert data["benchmark_hourly_rate"] == 150.0

    # Check by_role breakdown
    assert len(data["by_role"]) > 0
    roles = [r["role"] for r in data["by_role"]]
    assert "coding" in roles

    # Check recent_tasks
    assert len(data["recent_tasks"]) > 0
    task = data["recent_tasks"][0]
    assert "task_id" in task
    assert "title" in task
    assert "savings_rmb" in task
    assert task["savings_rmb"] > 0


@pytest.mark.asyncio
async def test_roi_days_filter(client, seed_tasks):
    """ROI endpoint respects the days parameter."""
    # Very old range should not include our recent tasks
    # (our task is 2 hours old, so days=0 wouldn't work but days=1 should)
    resp = await client.get("/api/v1/kpi/roi", params={"days": 1})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_roi_invalid_days(client):
    """ROI endpoint rejects invalid days parameter."""
    resp = await client.get("/api/v1/kpi/roi", params={"days": 0})
    assert resp.status_code == 422

    resp = await client.get("/api/v1/kpi/roi", params={"days": 999})
    assert resp.status_code == 422


# ── Cockpit Endpoint Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_cockpit_empty(client):
    """Cockpit endpoint returns valid response with no data."""
    resp = await client.get("/api/v1/kpi/cockpit")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending_gates_count" in data
    assert "running_tasks_count" in data
    assert "failed_tasks_today" in data
    assert "completed_tasks_today" in data
    assert "pending_gates" in data
    assert "running_tasks" in data
    assert "failed_tasks" in data
    assert "recent_completed" in data


@pytest.mark.asyncio
async def test_cockpit_with_data(client, seed_tasks):
    """Cockpit endpoint returns correct data with seeded records."""
    resp = await client.get("/api/v1/kpi/cockpit")
    assert resp.status_code == 200
    data = resp.json()

    # Pending gates
    assert data["pending_gates_count"] >= 1
    assert len(data["pending_gates"]) >= 1
    gate = next((g for g in data["pending_gates"] if g["id"] == "gate-cockpit-1"), None)
    assert gate is not None
    assert gate["status"] == "pending"

    # Running tasks
    assert data["running_tasks_count"] >= 1
    running = next((t for t in data["running_tasks"] if t["id"] == "cockpit-task-running"), None)
    assert running is not None
    assert running["current_stage"] == "coding"
    assert running["total_tokens"] == 15000

    # Failed tasks
    assert data["failed_tasks_today"] >= 1
    failed = next((t for t in data["failed_tasks"] if t["id"] == "cockpit-task-failed"), None)
    assert failed is not None
    assert "Test suite failed" in (failed["error_message"] or "")

    # Completed tasks
    assert data["completed_tasks_today"] >= 1
    assert len(data["recent_completed"]) >= 1


@pytest.mark.asyncio
async def test_cockpit_gate_structure(client, seed_tasks):
    """Cockpit gate items have the expected GateDetailResponse fields."""
    resp = await client.get("/api/v1/kpi/cockpit")
    data = resp.json()
    if data["pending_gates"]:
        gate = data["pending_gates"][0]
        assert "id" in gate
        assert "gate_type" in gate
        assert "task_id" in gate
        assert "agent_role" in gate
        assert "status" in gate
        assert "created_at" in gate


@pytest.mark.asyncio
async def test_cockpit_task_item_structure(client, seed_tasks):
    """Cockpit task items have the expected CockpitTaskItem fields."""
    resp = await client.get("/api/v1/kpi/cockpit")
    data = resp.json()
    if data["running_tasks"]:
        task = data["running_tasks"][0]
        expected_fields = ["id", "title", "status", "project_name", "created_at",
                          "current_stage", "total_tokens", "total_cost_rmb"]
        for field in expected_fields:
            assert field in task, f"Missing field: {field}"


# ── Retry Endpoint Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_retry_failed_task(client, seed_tasks):
    """Retrying a failed task resets failed stages to pending and resumes."""
    resp = await client.post("/api/v1/tasks/cockpit-task-failed/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["completed_at"] is None
    # Stages are preserved — failed stage reset to pending
    assert len(data["stages"]) == 1
    assert data["stages"][0]["status"] == "pending"
    assert data["stages"][0]["error_message"] is None
    assert data["stages"][0]["tokens_used"] == 0


@pytest.mark.asyncio
async def test_retry_non_failed_task(client, seed_tasks):
    """Retrying a non-failed task returns the task unchanged."""
    resp = await client.post("/api/v1/tasks/cockpit-task-running/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"  # unchanged


@pytest.mark.asyncio
async def test_retry_not_found(client):
    """Retrying a non-existent task returns 404."""
    resp = await client.post("/api/v1/tasks/nonexistent-id/retry")
    assert resp.status_code == 404


# ── Template estimated_hours Tests ────────────────────


@pytest_asyncio.fixture
async def seed_template_with_hours():
    """Seed a template with custom estimated_hours and a completed task using it."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            id="tmpl-custom-hours",
            name="custom_hours_template",
            display_name="Custom Hours Template",
            description="Template with custom estimated_hours",
            stages="[]",
            gates="[]",
            estimated_hours=2.0,
        )
        session.add(tmpl)

        t = TaskModel(
            id="roi-task-tmpl",
            title="Task with Template Hours",
            status="completed",
            total_tokens=10000,
            total_cost_rmb=0.1,
            template_id="tmpl-custom-hours",
            created_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=30),
        )
        session.add(t)
        await session.commit()

    yield

    # Cleanup
    async with async_session_factory() as session:
        for model_cls in [TaskModel, TaskTemplateModel]:
            result = await session.execute(select(model_cls))
            for obj in result.scalars().all():
                if obj.id.startswith(("roi-task-tmpl", "tmpl-")):
                    await session.delete(obj)
        await session.commit()


@pytest.mark.asyncio
async def test_roi_uses_template_estimated_hours(client, seed_template_with_hours):
    """ROI uses template estimated_hours when available."""
    resp = await client.get("/api/v1/kpi/roi", params={"days": 30})
    assert resp.status_code == 200
    data = resp.json()

    # Find the task with template
    task = next(
        (t for t in data["recent_tasks"] if t["task_id"] == "roi-task-tmpl"),
        None,
    )
    assert task is not None
    # Should use 2.0h from template, not 8.0h global default
    assert task["estimated_manual_hours"] == 2.0
    # estimated_manual_rmb = 2.0 * 150.0 = 300.0
    assert task["estimated_manual_rmb"] == 300.0


@pytest.mark.asyncio
async def test_template_estimated_hours_in_schema(client):
    """Template CRUD includes estimated_hours field."""
    # Create template with estimated_hours
    resp = await client.post("/api/v1/templates", json={
        "name": "tmpl-test-hours",
        "display_name": "Test Hours Template",
        "stages": [],
        "gates": [],
        "estimated_hours": 4.5,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["estimated_hours"] == 4.5

    # Verify via GET
    tmpl_id = data["id"]
    resp = await client.get(f"/api/v1/templates/{tmpl_id}")
    assert resp.status_code == 200
    assert resp.json()["estimated_hours"] == 4.5

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.id == tmpl_id)
        )
        tmpl = result.scalar_one_or_none()
        if tmpl:
            await session.delete(tmpl)
            await session.commit()
