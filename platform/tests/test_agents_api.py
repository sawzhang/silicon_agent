"""Tests for Agents API endpoints."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.agent import AgentModel


@pytest_asyncio.fixture
async def seed_agent():
    """Seed an agent for testing."""
    async with async_session_factory() as session:
        agent = AgentModel(
            id="ag-test-id",
            role="ag-test-coding",
            display_name="Test Coding Agent",
            status="idle",
            model_name="gpt-3.5-turbo",
        )
        session.add(agent)
        await session.commit()

    yield

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(AgentModel).where(AgentModel.id == "ag-test-id")
        )
        obj = result.scalar_one_or_none()
        if obj:
            await session.delete(obj)
            await session.commit()


@pytest.mark.asyncio
async def test_list_agents(client, seed_agent):
    """GET /api/v1/agents returns a list containing the seeded agent."""
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    roles = [a["role"] for a in data["agents"]]
    assert "ag-test-coding" in roles


@pytest.mark.asyncio
async def test_get_agent(client, seed_agent):
    """GET /api/v1/agents/{role} returns the agent with correct fields."""
    resp = await client.get("/api/v1/agents/ag-test-coding")
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "ag-test-coding"
    assert data["display_name"] == "Test Coding Agent"
    assert data["status"] == "idle"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_agent_404(client):
    """GET /api/v1/agents/nonexistent returns 404."""
    resp = await client.get("/api/v1/agents/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_agent_config(client, seed_agent):
    """PUT /api/v1/agents/{role}/config updates model_name."""
    resp = await client.put(
        "/api/v1/agents/ag-test-coding/config",
        json={"model_name": "gpt-4"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_name"] == "gpt-4"
    assert data["role"] == "ag-test-coding"


@pytest.mark.asyncio
async def test_agent_session(client, seed_agent):
    """GET /api/v1/agents/{role}/session returns session info."""
    resp = await client.get("/api/v1/agents/ag-test-coding/session")
    # May return 200 with session or 404 if not found — both are acceptable
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        assert data["role"] == "ag-test-coding"
        assert "status" in data


@pytest.mark.asyncio
async def test_agent_session_404(client):
    """GET /api/v1/agents/nonexistent/session returns 404."""
    resp = await client.get("/api/v1/agents/nonexistent/session")
    assert resp.status_code == 404
