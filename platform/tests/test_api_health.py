"""Smoke tests for core API endpoints."""
import pytest


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_list_agents(client):
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data


@pytest.mark.asyncio
async def test_list_gates(client):
    resp = await client.get("/api/v1/gates")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_list_tasks(client):
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_auth_token(client):
    resp = await client.post("/api/v1/auth/token", json={"username": "testuser"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_list_templates(client):
    resp = await client.get("/api/v1/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
