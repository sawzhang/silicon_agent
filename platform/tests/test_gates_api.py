"""Tests for Gates API endpoints."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.gate import HumanGateModel
from app.models.task import TaskModel


@pytest_asyncio.fixture
async def seed_gates():
    """Seed a task with a pending and an approved gate."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        task = TaskModel(
            id="gt-task-1",
            title="Gate Test Task",
            status="running",
            total_tokens=1000,
            total_cost_rmb=0.01,
            created_at=now,
        )
        session.add(task)

        pending_gate = HumanGateModel(
            id="gt-gate-pending",
            gate_type="review",
            task_id="gt-task-1",
            agent_role="review",
            status="pending",
            content={"stage": "review", "summary": "Please review"},
        )
        session.add(pending_gate)

        approved_gate = HumanGateModel(
            id="gt-gate-approved",
            gate_type="deploy",
            task_id="gt-task-1",
            agent_role="deploy",
            status="approved",
            reviewer="admin",
            review_comment="Looks good",
            reviewed_at=now,
            content={"stage": "deploy", "summary": "Deploy approval"},
        )
        session.add(approved_gate)

        await session.commit()

    yield

    # Cleanup: delete gates first, then tasks (FK constraint)
    async with async_session_factory() as session:
        for model_cls in [HumanGateModel, TaskModel]:
            result = await session.execute(select(model_cls))
            for obj in result.scalars().all():
                if obj.id.startswith("gt-"):
                    await session.delete(obj)
        await session.commit()


# ── List Gates ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_gates(client, seed_gates):
    """GET /api/v1/gates returns paginated list with correct structure."""
    resp = await client.get("/api/v1/gates")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


@pytest.mark.asyncio
async def test_list_gates_status_filter(client, seed_gates):
    """GET /api/v1/gates?status=pending returns only pending gates."""
    resp = await client.get("/api/v1/gates", params={"status": "pending"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["status"] == "pending"


# ── Get Single Gate ───────────────────────────────────────


@pytest.mark.asyncio
async def test_get_gate(client, seed_gates):
    """GET /api/v1/gates/{id} returns full gate detail."""
    resp = await client.get("/api/v1/gates/gt-gate-pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "gt-gate-pending"
    assert data["gate_type"] == "review"
    assert data["task_id"] == "gt-task-1"
    assert data["agent_role"] == "review"
    assert data["status"] == "pending"
    assert data["content"] == {"stage": "review", "summary": "Please review"}
    assert data["reviewer"] is None
    assert data["review_comment"] is None
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_gate_404(client, seed_gates):
    """GET /api/v1/gates/nonexistent returns 404."""
    resp = await client.get("/api/v1/gates/nonexistent")
    assert resp.status_code == 404


# ── Approve / Reject ─────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_gate(client, seed_gates):
    """POST /api/v1/gates/{id}/approve transitions a pending gate to approved."""
    # Seed a fresh pending gate for this test
    async with async_session_factory() as session:
        gate = HumanGateModel(
            id="gt-gate-approve-test",
            gate_type="review",
            task_id="gt-task-1",
            agent_role="review",
            status="pending",
            content={"stage": "review", "summary": "Approve me"},
        )
        session.add(gate)
        await session.commit()

    resp = await client.post(
        "/api/v1/gates/gt-gate-approve-test/approve",
        json={"reviewer": "tester"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["reviewer"] == "tester"
    assert data["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_reject_gate(client, seed_gates):
    """POST /api/v1/gates/{id}/reject transitions a pending gate to rejected."""
    # Seed a fresh pending gate for this test
    async with async_session_factory() as session:
        gate = HumanGateModel(
            id="gt-gate-reject-test",
            gate_type="review",
            task_id="gt-task-1",
            agent_role="review",
            status="pending",
            content={"stage": "review", "summary": "Reject me"},
        )
        session.add(gate)
        await session.commit()

    resp = await client.post(
        "/api/v1/gates/gt-gate-reject-test/reject",
        json={"reviewer": "tester", "comment": "needs fix"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["reviewer"] == "tester"
    assert data["review_comment"] == "needs fix"
    assert data["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_approve_already_resolved(client, seed_gates):
    """Approving an already-approved gate still succeeds (service overwrites)."""
    resp = await client.post(
        "/api/v1/gates/gt-gate-approved/approve",
        json={"reviewer": "tester"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["reviewer"] == "tester"


# ── Gate History ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_history(client, seed_gates):
    """GET /api/v1/gates/history returns only approved/rejected gates."""
    resp = await client.get("/api/v1/gates/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    # The seeded approved gate should appear in history
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["status"] in ("approved", "rejected")
