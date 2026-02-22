"""Tests for Circuit Breaker API endpoints."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.audit import CircuitBreakerModel


@pytest_asyncio.fixture(autouse=True)
async def cleanup_circuit_breakers():
    """Clean up circuit breaker records after each test."""
    yield

    async with async_session_factory() as session:
        result = await session.execute(select(CircuitBreakerModel))
        for obj in result.scalars().all():
            if obj.triggered_by and obj.triggered_by.startswith("cb-test"):
                await session.delete(obj)
        await session.commit()


@pytest.mark.asyncio
async def test_list_circuit_breakers(client):
    """GET /api/v1/circuit-breaker returns a list response."""
    resp = await client.get("/api/v1/circuit-breaker")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_trigger_circuit_breaker(client):
    """POST /api/v1/circuit-breaker/trigger creates a circuit breaker."""
    resp = await client.post(
        "/api/v1/circuit-breaker/trigger",
        json={"level": 1, "triggered_by": "cb-test-trigger", "reason": "test trigger"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["level"] == 1
    assert data["status"] == "triggered"
    assert data["triggered_by"] == "cb-test-trigger"
    assert data["trigger_reason"] == "test trigger"
    assert data["triggered_at"] is not None
    assert "id" in data


@pytest.mark.asyncio
async def test_resolve_circuit_breaker(client):
    """Trigger then resolve a circuit breaker."""
    # First trigger one
    trigger_resp = await client.post(
        "/api/v1/circuit-breaker/trigger",
        json={"level": 2, "triggered_by": "cb-test-resolve", "reason": "test resolve"},
    )
    assert trigger_resp.status_code == 201
    cb_id = trigger_resp.json()["id"]

    # Resolve it
    resolve_resp = await client.post(
        "/api/v1/circuit-breaker/resolve",
        json={"id": cb_id, "resolved_by": "admin"},
    )
    assert resolve_resp.status_code == 200
    data = resolve_resp.json()
    assert data["id"] == cb_id
    assert data["status"] == "resolved"
    assert data["resolved_by"] == "admin"
    assert data["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_not_found(client):
    """POST /api/v1/circuit-breaker/resolve with nonexistent id returns 404."""
    resp = await client.post(
        "/api/v1/circuit-breaker/resolve",
        json={"id": "nonexistent-cb-id", "resolved_by": "admin"},
    )
    assert resp.status_code == 404
