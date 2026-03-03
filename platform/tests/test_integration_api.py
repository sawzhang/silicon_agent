"""Tests for Integration API endpoints (project-level)."""
from __future__ import annotations

import pytest
import pytest_asyncio
from uuid import uuid4

from app.db.session import async_session_factory
from app.models.project import ProjectModel
from app.models.integration import ProjectIntegrationModel


def _unique_name() -> str:
    return f"integ-test-{uuid4().hex[:12]}"


@pytest_asyncio.fixture
async def project_for_integration(client):
    """Create a project for integration tests and clean up after."""
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": f"Display {name}",
    })
    assert resp.status_code == 201
    data = resp.json()
    yield data

    # Cleanup: delete integrations first (cascade should handle it, but be safe)
    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(
            delete(ProjectIntegrationModel).where(
                ProjectIntegrationModel.project_id == data["id"]
            )
        )
        proj = await session.get(ProjectModel, data["id"])
        if proj:
            await session.delete(proj)
        await session.commit()


# ── Create ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_integration(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "github",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["provider"] == "github"
    assert data["project_id"] == pid
    assert data["enabled"] is True
    assert data["webhook_secret"]  # auto-generated
    assert data["webhook_url"] == f"/webhooks/github/{pid}"
    assert data["access_token"] is None


@pytest.mark.asyncio
async def test_create_integration_with_token(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "jira",
        "access_token": "my_secret_token_1234",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["provider"] == "jira"
    # access_token should be masked
    assert data["access_token"] == "****1234"


@pytest.mark.asyncio
async def test_create_integration_invalid_provider(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "bitbucket",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_integration_duplicate(client, project_for_integration):
    pid = project_for_integration["id"]
    resp1 = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "github",
    })
    assert resp1.status_code == 201

    resp2 = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "github",
    })
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_create_integration_project_not_found(client):
    resp = await client.post("/api/v1/projects/nonexistent-id/integrations", json={
        "provider": "github",
    })
    assert resp.status_code == 404


# ── List ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_integrations_empty(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.get(f"/api/v1/projects/{pid}/integrations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_integrations_with_data(client, project_for_integration):
    pid = project_for_integration["id"]
    await client.post(f"/api/v1/projects/{pid}/integrations", json={"provider": "github"})
    await client.post(f"/api/v1/projects/{pid}/integrations", json={"provider": "jira"})

    resp = await client.get(f"/api/v1/projects/{pid}/integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    providers = {i["provider"] for i in data}
    assert providers == {"github", "jira"}


# ── Get ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_integration(client, project_for_integration):
    pid = project_for_integration["id"]
    await client.post(f"/api/v1/projects/{pid}/integrations", json={"provider": "gitlab"})

    resp = await client.get(f"/api/v1/projects/{pid}/integrations/gitlab")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "gitlab"


@pytest.mark.asyncio
async def test_get_integration_not_found(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.get(f"/api/v1/projects/{pid}/integrations/github")
    assert resp.status_code == 404


# ── Update ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_integration(client, project_for_integration):
    pid = project_for_integration["id"]
    await client.post(f"/api/v1/projects/{pid}/integrations", json={"provider": "github"})

    resp = await client.put(f"/api/v1/projects/{pid}/integrations/github", json={
        "enabled": False,
        "access_token": "new_token_abcd",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["access_token"] == "****abcd"


@pytest.mark.asyncio
async def test_update_integration_not_found(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.put(f"/api/v1/projects/{pid}/integrations/github", json={
        "enabled": False,
    })
    assert resp.status_code == 404


# ── Delete ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_integration(client, project_for_integration):
    pid = project_for_integration["id"]
    await client.post(f"/api/v1/projects/{pid}/integrations", json={"provider": "github"})

    resp = await client.delete(f"/api/v1/projects/{pid}/integrations/github")
    assert resp.status_code == 204

    # Verify gone
    resp = await client.get(f"/api/v1/projects/{pid}/integrations/github")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_integration_not_found(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.delete(f"/api/v1/projects/{pid}/integrations/github")
    assert resp.status_code == 404


# ── Regenerate Secret ───────────────────────────────────


@pytest.mark.asyncio
async def test_regenerate_secret(client, project_for_integration):
    pid = project_for_integration["id"]
    create_resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "github",
    })
    old_secret = create_resp.json()["webhook_secret"]

    resp = await client.post(f"/api/v1/projects/{pid}/integrations/github/regenerate-secret")
    assert resp.status_code == 200
    new_secret = resp.json()["webhook_secret"]
    assert new_secret != old_secret


@pytest.mark.asyncio
async def test_regenerate_secret_not_found(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.post(f"/api/v1/projects/{pid}/integrations/github/regenerate-secret")
    assert resp.status_code == 404


# ── Project-level trigger endpoints ─────────────────────


@pytest.mark.asyncio
async def test_list_project_triggers_empty(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.get(f"/api/v1/projects/{pid}/triggers")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_project_events_empty(client, project_for_integration):
    pid = project_for_integration["id"]
    resp = await client.get(f"/api/v1/projects/{pid}/triggers/events")
    assert resp.status_code == 200
    assert resp.json() == []
