"""Tests for the Templates API endpoints."""
import pytest
import pytest_asyncio

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.template import TaskTemplateModel


@pytest_asyncio.fixture
async def seed_builtin_template():
    """Seed a builtin template for testing builtin protection."""
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            id="tmpl-test-builtin-1",
            name="tmpl-test-builtin-pipeline",
            display_name="Builtin Test Pipeline",
            description="A builtin template for tests",
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[]',
            is_builtin=True,
        )
        session.add(tmpl)
        await session.commit()

    yield

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(
                TaskTemplateModel.id.like("tmpl-test-%")
            )
        )
        for obj in result.scalars().all():
            await session.delete(obj)
        await session.commit()


# ── CRUD Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_template(client):
    """POST /api/v1/templates creates a new template and returns 201."""
    payload = {
        "name": "tmpl-test-create",
        "display_name": "Create Test Template",
        "description": "Created via test",
        "stages": [
            {"name": "code", "agent_role": "coding", "order": 0},
            {"name": "test", "agent_role": "test", "order": 1},
        ],
        "gates": [
            {"after_stage": "code", "type": "human_approve"},
        ],
        "estimated_hours": 3.5,
    }
    resp = await client.post("/api/v1/templates", json=payload)
    assert resp.status_code == 201

    data = resp.json()
    assert data["name"] == "tmpl-test-create"
    assert data["display_name"] == "Create Test Template"
    assert data["description"] == "Created via test"
    assert len(data["stages"]) == 2
    assert data["stages"][0]["name"] == "code"
    assert len(data["gates"]) == 1
    assert data["gates"][0]["after_stage"] == "code"
    assert data["estimated_hours"] == 3.5
    assert data["is_builtin"] is False
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data

    # Cleanup
    tmpl_id = data["id"]
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.id == tmpl_id)
        )
        tmpl = result.scalar_one_or_none()
        if tmpl:
            await session.delete(tmpl)
            await session.commit()


@pytest.mark.asyncio
async def test_get_template(client):
    """GET /api/v1/templates/{id} returns the correct template."""
    # Create a template first
    payload = {
        "name": "tmpl-test-get",
        "display_name": "Get Test Template",
        "stages": [{"name": "parse", "agent_role": "orchestrator", "order": 0}],
        "gates": [],
    }
    create_resp = await client.post("/api/v1/templates", json=payload)
    assert create_resp.status_code == 201
    tmpl_id = create_resp.json()["id"]

    # Fetch it
    resp = await client.get(f"/api/v1/templates/{tmpl_id}")
    assert resp.status_code == 200

    data = resp.json()
    assert data["id"] == tmpl_id
    assert data["name"] == "tmpl-test-get"
    assert data["display_name"] == "Get Test Template"
    assert len(data["stages"]) == 1
    assert data["stages"][0]["agent_role"] == "orchestrator"
    assert data["is_builtin"] is False

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.id == tmpl_id)
        )
        tmpl = result.scalar_one_or_none()
        if tmpl:
            await session.delete(tmpl)
            await session.commit()


@pytest.mark.asyncio
async def test_get_template_404(client):
    """GET /api/v1/templates/{id} returns 404 for nonexistent template."""
    resp = await client.get("/api/v1/templates/nonexistent-tmpl-id")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_templates_with_builtins(client, seed_builtin_template):
    """GET /api/v1/templates returns list including builtin templates."""
    resp = await client.get("/api/v1/templates")
    assert resp.status_code == 200

    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1

    # Find our seeded builtin template
    builtin = next(
        (t for t in data["items"] if t["id"] == "tmpl-test-builtin-1"), None
    )
    assert builtin is not None
    assert builtin["is_builtin"] is True
    assert builtin["name"] == "tmpl-test-builtin-pipeline"


@pytest.mark.asyncio
async def test_update_template(client):
    """PUT /api/v1/templates/{id} updates a non-builtin template."""
    # Create a template
    payload = {
        "name": "tmpl-test-update",
        "display_name": "Update Test Original",
        "stages": [],
        "gates": [],
    }
    create_resp = await client.post("/api/v1/templates", json=payload)
    assert create_resp.status_code == 201
    tmpl_id = create_resp.json()["id"]

    # Update it
    update_payload = {
        "display_name": "Update Test Modified",
        "description": "Now has a description",
        "estimated_hours": 5.0,
    }
    resp = await client.put(f"/api/v1/templates/{tmpl_id}", json=update_payload)
    assert resp.status_code == 200

    data = resp.json()
    assert data["display_name"] == "Update Test Modified"
    assert data["description"] == "Now has a description"
    assert data["estimated_hours"] == 5.0
    # Name should remain unchanged
    assert data["name"] == "tmpl-test-update"

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.id == tmpl_id)
        )
        tmpl = result.scalar_one_or_none()
        if tmpl:
            await session.delete(tmpl)
            await session.commit()


@pytest.mark.asyncio
async def test_update_builtin_template(client, seed_builtin_template):
    """PUT /api/v1/templates/{id} on a builtin template does not apply changes."""
    # Try to update the builtin template
    update_payload = {
        "display_name": "Should Not Change",
        "description": "Should not be applied",
    }
    resp = await client.put(
        "/api/v1/templates/tmpl-test-builtin-1", json=update_payload
    )
    # The service returns the template unchanged (no error raised) so API returns 200
    assert resp.status_code == 200

    data = resp.json()
    # Builtin template should NOT have the new display_name
    assert data["display_name"] == "Builtin Test Pipeline"
    assert data["is_builtin"] is True


@pytest.mark.asyncio
async def test_delete_template(client):
    """DELETE /api/v1/templates/{id} removes a non-builtin template."""
    # Create a template
    payload = {
        "name": "tmpl-test-delete",
        "display_name": "Delete Test Template",
        "stages": [],
        "gates": [],
    }
    create_resp = await client.post("/api/v1/templates", json=payload)
    assert create_resp.status_code == 201
    tmpl_id = create_resp.json()["id"]

    # Delete it
    resp = await client.delete(f"/api/v1/templates/{tmpl_id}")
    assert resp.status_code == 204

    # Confirm it is gone
    get_resp = await client.get(f"/api/v1/templates/{tmpl_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_builtin_template(client, seed_builtin_template):
    """DELETE /api/v1/templates/{id} on a builtin template returns 404."""
    resp = await client.delete("/api/v1/templates/tmpl-test-builtin-1")
    # The service returns False for builtin -> API raises 404 "not found or is builtin"
    assert resp.status_code == 404
    assert "builtin" in resp.json()["detail"].lower()
