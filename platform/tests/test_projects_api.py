"""Tests for Projects API endpoints."""
import pytest
import pytest_asyncio
from uuid import uuid4

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.project import ProjectModel


def _unique_name() -> str:
    return f"proj-test-{uuid4().hex[:12]}"


@pytest_asyncio.fixture
async def created_project(client):
    """Create a project via API and clean up after the test."""
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": f"Display {name}",
        "description": "fixture project",
    })
    assert resp.status_code == 201
    data = resp.json()
    yield data

    # Cleanup
    await client.delete(f"/api/v1/projects/{data['id']}")


async def _cleanup_project(project_id: str):
    """Delete a project directly from the DB."""
    async with async_session_factory() as session:
        proj = await session.get(ProjectModel, project_id)
        if proj:
            await session.delete(proj)
            await session.commit()


# ── Create ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project(client):
    """POST /api/v1/projects creates a project and returns 201."""
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": "My Test Project",
        "repo_url": "https://github.com/example/repo",
        "branch": "develop",
        "description": "A test project",
    })
    assert resp.status_code == 201
    data = resp.json()

    assert data["name"] == name
    assert data["display_name"] == "My Test Project"
    assert data["repo_url"] == "https://github.com/example/repo"
    assert data["branch"] == "develop"
    assert data["description"] == "A test project"
    assert data["status"] == "active"
    assert data["id"] is not None
    assert data["created_at"] is not None
    assert data["updated_at"] is not None

    # Cleanup
    await _cleanup_project(data["id"])


# ── Read ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_project(client, created_project):
    """GET /api/v1/projects/{id} returns the project."""
    project_id = created_project["id"]
    resp = await client.get(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == project_id
    assert data["name"] == created_project["name"]
    assert data["display_name"] == created_project["display_name"]
    assert data["status"] == "active"
    assert "created_at" in data
    assert "updated_at" in data
    assert "tech_stack" in data
    assert "repo_tree" in data
    assert "last_synced_at" in data


@pytest.mark.asyncio
async def test_get_project_404(client):
    """GET /api/v1/projects/nonexistent returns 404."""
    resp = await client.get("/api/v1/projects/nonexistent-id-999")
    assert resp.status_code == 404


# ── List ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_projects(client):
    """GET /api/v1/projects returns created projects."""
    name1 = _unique_name()
    name2 = _unique_name()

    resp1 = await client.post("/api/v1/projects", json={
        "name": name1, "display_name": f"Display {name1}",
    })
    resp2 = await client.post("/api/v1/projects", json={
        "name": name2, "display_name": f"Display {name2}",
    })
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    id1 = resp1.json()["id"]
    id2 = resp2.json()["id"]

    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    data = resp.json()

    assert "items" in data
    assert "total" in data
    assert data["total"] >= 2

    ids_in_list = [p["id"] for p in data["items"]]
    assert id1 in ids_in_list
    assert id2 in ids_in_list

    # Cleanup
    await _cleanup_project(id1)
    await _cleanup_project(id2)


# ── Update ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_project(client, created_project):
    """PUT /api/v1/projects/{id} updates and returns the project."""
    project_id = created_project["id"]
    resp = await client.put(f"/api/v1/projects/{project_id}", json={
        "display_name": "Updated Display Name",
        "description": "Updated description",
        "status": "archived",
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == project_id
    assert data["display_name"] == "Updated Display Name"
    assert data["description"] == "Updated description"
    assert data["status"] == "archived"
    # Unchanged fields stay the same
    assert data["name"] == created_project["name"]


# ── Delete ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_project(client):
    """DELETE /api/v1/projects/{id} returns 204 and removes the project."""
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name, "display_name": f"Display {name}",
    })
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    # Delete
    resp = await client.delete(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 204

    # Verify gone
    resp = await client.get(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_404(client):
    """DELETE /api/v1/projects/nonexistent returns 404."""
    resp = await client.delete("/api/v1/projects/nonexistent-id-999")
    assert resp.status_code == 404


# ── Pagination ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_projects_pagination(client):
    """GET /api/v1/projects respects page and page_size params."""
    names = [_unique_name() for _ in range(3)]
    ids = []
    for name in names:
        resp = await client.post("/api/v1/projects", json={
            "name": name, "display_name": f"Display {name}",
        })
        assert resp.status_code == 201
        ids.append(resp.json()["id"])

    # Page 1, size 2 -> should return 2 items
    resp = await client.get("/api/v1/projects", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2
    assert data["total"] >= 3

    # Page 2, size 2 -> should return at least 1 item
    resp = await client.get("/api/v1/projects", params={"page": 2, "page_size": 2})
    assert resp.status_code == 200
    data2 = resp.json()
    assert len(data2["items"]) >= 1

    # Pages should not overlap
    page1_ids = {p["id"] for p in data["items"]}
    page2_ids = {p["id"] for p in data2["items"]}
    assert page1_ids.isdisjoint(page2_ids)

    # Cleanup
    for pid in ids:
        await _cleanup_project(pid)
