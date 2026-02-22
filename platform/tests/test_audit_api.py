"""Tests for Audit API endpoints."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.audit import AuditLogModel


@pytest_asyncio.fixture
async def seed_audit_logs():
    """Seed audit log records for testing."""
    async with async_session_factory() as session:
        log1 = AuditLogModel(
            id="audit-test-1",
            agent_role="coding",
            action_type="stage_coding_completed",
            action_detail={"task_id": "t1"},
            risk_level="low",
        )
        log2 = AuditLogModel(
            id="audit-test-2",
            agent_role="test",
            action_type="stage_test_completed",
            action_detail={"task_id": "t2"},
            risk_level="medium",
        )
        session.add_all([log1, log2])
        await session.commit()

    yield

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLogModel).where(
                AuditLogModel.id.in_(["audit-test-1", "audit-test-2"])
            )
        )
        for obj in result.scalars().all():
            await session.delete(obj)
        await session.commit()


@pytest.mark.asyncio
async def test_list_audit_logs(client, seed_audit_logs):
    """GET /api/v1/audit/logs returns paginated audit logs."""
    resp = await client.get("/api/v1/audit/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_audit_logs_filter(client, seed_audit_logs):
    """GET /api/v1/audit/logs?agent_role=coding filters by role."""
    resp = await client.get("/api/v1/audit/logs", params={"agent_role": "coding"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    for item in data["items"]:
        assert item["agent_role"] == "coding"


@pytest.mark.asyncio
async def test_get_audit_log(client, seed_audit_logs):
    """GET /api/v1/audit/logs/{id} returns the specific audit log."""
    resp = await client.get("/api/v1/audit/logs/audit-test-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "audit-test-1"
    assert data["agent_role"] == "coding"
    assert data["action_type"] == "stage_coding_completed"
    assert data["risk_level"] == "low"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_audit_log_404(client):
    """GET /api/v1/audit/logs/nonexistent returns 404."""
    resp = await client.get("/api/v1/audit/logs/nonexistent")
    assert resp.status_code == 404
