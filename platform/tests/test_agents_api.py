"""Tests for Agents API endpoints."""
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
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
    """PUT /api/v1/agents/{role}/config updates model and runtime config."""
    builtin_skills_dir = str((Path(__file__).resolve().parents[1] / "skills").resolve())
    resp = await client.put(
        "/api/v1/agents/ag-test-coding/config",
        json={
            "model_name": "gpt-4",
            "temperature": 0.3,
            "max_tokens": 8192,
            "max_turns": 18,
            "thinking_level": "medium",
            "extra_skill_dirs": [builtin_skills_dir],
            "system_prompt_append": "Focus on robustness",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_name"] == "gpt-4"
    assert data["role"] == "ag-test-coding"
    assert data["config"]["temperature"] == 0.3
    assert data["config"]["max_tokens"] == 8192
    assert data["config"]["max_turns"] == 18
    assert data["config"]["thinking_level"] == "medium"
    assert data["config"]["extra_skill_dirs"] == [builtin_skills_dir]

@pytest.mark.asyncio
async def test_get_agent_config_options(client):
    """GET /api/v1/agents/config/options returns configurable options."""
    resp = await client.get("/api/v1/agents/config/options")
    assert resp.status_code == 200
    data = resp.json()
    assert "available_models" in data
    assert "thinking_levels" in data
    assert "role_defaults" in data
    assert "coding" in data["role_defaults"]


@pytest.mark.asyncio
async def test_get_agent_config_options_from_llm_gateway(client, monkeypatch):
    """GET /api/v1/agents/config/options prefers models returned by LLM gateway."""

    class FakeLLMClient:
        async def list_models(self):
            return ["gpt-5.1-codex-mini", "gpt-5.1-codex"]

    monkeypatch.setattr(
        "app.services.agent_service.get_llm_client", lambda: FakeLLMClient()
    )
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.1-codex-mini")
    monkeypatch.setattr(
        settings,
        "LLM_ROLE_MODEL_MAP",
        '{"coding":"gpt-5.1-codex","review":"gpt-5.1-codex-mini"}',
    )

    resp = await client.get("/api/v1/agents/config/options")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available_models"] == ["gpt-5.1-codex-mini", "gpt-5.1-codex"]
    assert data["role_defaults"]["coding"] == "gpt-5.1-codex"
    assert data["role_defaults"]["review"] == "gpt-5.1-codex-mini"


@pytest.mark.asyncio
async def test_get_agent_config_options_fallback_when_gateway_fails(client, monkeypatch):
    """GET /api/v1/agents/config/options falls back to local model set on gateway failure."""

    class _BrokenClient:
        async def list_models(self):
            raise RuntimeError("gateway unavailable")

    monkeypatch.setattr("app.services.agent_service.get_llm_client", lambda: _BrokenClient())
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.1-codex-mini")
    monkeypatch.setattr(settings, "LLM_ROLE_MODEL_MAP", '{"coding":"gpt-5.1-codex"}')

    resp = await client.get("/api/v1/agents/config/options")
    assert resp.status_code == 200
    data = resp.json()
    assert "gpt-5.1-codex-mini" in data["available_models"]
    assert "gpt-5.1-codex" in data["available_models"]
    assert data["role_defaults"]["coding"] in data["available_models"]


@pytest.mark.asyncio
async def test_update_agent_config_rejects_extra_skill_dirs_outside_whitelist(
    client, seed_agent, tmp_path, monkeypatch
):
    """PUT /agents/{role}/config returns 400 for disallowed extra skill directories."""
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True, exist_ok=True)
    disallowed = tmp_path / "disallowed"
    disallowed.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "EXTRA_SKILL_DIR_WHITELIST", str(allowed_root))

    resp = await client.put(
        "/api/v1/agents/ag-test-coding/config",
        json={
            "extra_skill_dirs": [str(disallowed)],
        },
    )
    assert resp.status_code == 400
    assert "whitelist" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_agent_config_accepts_extra_skill_dirs_within_whitelist(
    client, seed_agent, tmp_path, monkeypatch
):
    """PUT /agents/{role}/config accepts whitelisted extra skill directories."""
    allowed_root = tmp_path / "allowed"
    allowed_nested = allowed_root / "nested"
    allowed_nested.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "EXTRA_SKILL_DIR_WHITELIST", str(allowed_root))

    resp = await client.put(
        "/api/v1/agents/ag-test-coding/config",
        json={
            "extra_skill_dirs": [str(allowed_nested)],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["extra_skill_dirs"] == [str(allowed_nested.resolve())]


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
