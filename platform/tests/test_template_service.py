"""Tests for TemplateService — maximise coverage of template_service.py."""
from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.db.session import async_session_factory
from app.models.template import TaskTemplateModel
from app.services.template_service import TemplateService
from app.schemas.template import (
    TemplateCreateRequest,
    TemplateUpdateRequest,
    StageDefinition,
    GateDefinition,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _unique_name(prefix: str = "svc-tmpl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _delete_by_name_prefix(prefix: str) -> None:
    """Remove all templates whose name starts with prefix."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.name.like(f"{prefix}%"))
        )
        for obj in result.scalars().all():
            await session.delete(obj)
        await session.commit()


async def _delete_by_id(template_id: str) -> None:
    async with async_session_factory() as session:
        obj = await session.get(TaskTemplateModel, template_id)
        if obj:
            await session.delete(obj)
            await session.commit()


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def service():
    """Return a TemplateService backed by a live test session."""
    async with async_session_factory() as session:
        yield TemplateService(session)


@pytest_asyncio.fixture
async def custom_template():
    """Create a non-builtin template directly in DB and clean up after."""
    name = _unique_name("svc-custom")
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            name=name,
            display_name="Custom Template",
            description="For service tests",
            stages=json.dumps([{"name": "code", "agent_role": "coding", "order": 0}]),
            gates=json.dumps([]),
            is_builtin=False,
        )
        session.add(tmpl)
        await session.commit()
        await session.refresh(tmpl)
        tmpl_id = tmpl.id

    yield {"id": tmpl_id, "name": name}

    await _delete_by_id(tmpl_id)


@pytest_asyncio.fixture
async def builtin_template():
    """Create a builtin template directly in DB and clean up after."""
    name = _unique_name("svc-builtin")
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            name=name,
            display_name="Builtin Template",
            description="Builtin for service tests",
            stages=json.dumps([{"name": "parse", "agent_role": "orchestrator", "order": 0}]),
            gates=json.dumps([]),
            is_builtin=True,
        )
        session.add(tmpl)
        await session.commit()
        await session.refresh(tmpl)
        tmpl_id = tmpl.id
        tmpl_name = tmpl.name

    yield {"id": tmpl_id, "name": tmpl_name}

    await _delete_by_id(tmpl_id)


# ── list_templates ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_templates_returns_items(service, custom_template):
    """list_templates returns a TemplateListResponse with the created template."""
    resp = await service.list_templates()
    ids = [t.id for t in resp.items]
    assert custom_template["id"] in ids
    assert resp.total >= 1


@pytest.mark.asyncio
async def test_list_templates_versioning_filter():
    """When TEMPLATE_VERSIONING_ENABLED=True, only active templates are returned."""
    original = settings.TEMPLATE_VERSIONING_ENABLED
    name_active = _unique_name("svc-v-active")
    name_inactive = _unique_name("svc-v-inactive")
    active_id = None
    inactive_id = None
    try:
        # Seed one active and one inactive template
        async with async_session_factory() as session:
            t_active = TaskTemplateModel(
                name=name_active,
                display_name="Active",
                stages="[]",
                gates="[]",
                is_active=True,
            )
            t_inactive = TaskTemplateModel(
                name=name_inactive,
                display_name="Inactive",
                stages="[]",
                gates="[]",
                is_active=False,
            )
            session.add(t_active)
            session.add(t_inactive)
            await session.commit()
            await session.refresh(t_active)
            await session.refresh(t_inactive)
            active_id = t_active.id
            inactive_id = t_inactive.id

        settings.TEMPLATE_VERSIONING_ENABLED = True
        async with async_session_factory() as session:
            svc = TemplateService(session)
            resp = await svc.list_templates()

        ids = [t.id for t in resp.items]
        assert active_id in ids
        assert inactive_id not in ids
    finally:
        settings.TEMPLATE_VERSIONING_ENABLED = original
        if active_id:
            await _delete_by_id(active_id)
        if inactive_id:
            await _delete_by_id(inactive_id)


# ── get_template ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_template_found(service, custom_template):
    """get_template returns a TemplateResponse for a known ID."""
    resp = await service.get_template(custom_template["id"])
    assert resp is not None
    assert resp.id == custom_template["id"]
    assert resp.name == custom_template["name"]


@pytest.mark.asyncio
async def test_get_template_not_found(service):
    """get_template returns None for an unknown ID."""
    resp = await service.get_template("nonexistent-id-xyz")
    assert resp is None


# ── create_template ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_template_basic(service):
    """create_template persists a template and returns a valid response."""
    name = _unique_name("svc-create")
    request = TemplateCreateRequest(
        name=name,
        display_name="Service Create Test",
        description="desc",
        stages=[StageDefinition(name="code", agent_role="coding", order=0)],
        gates=[GateDefinition(after_stage="code", type="human_approve")],
        estimated_hours=2.5,
    )
    resp = await service.create_template(request)

    assert resp.id is not None
    assert resp.name == name
    assert resp.display_name == "Service Create Test"
    assert resp.description == "desc"
    assert len(resp.stages) == 1
    assert resp.stages[0]["name"] == "code"
    assert len(resp.gates) == 1
    assert resp.gates[0]["after_stage"] == "code"
    assert resp.estimated_hours == 2.5
    assert resp.is_builtin is False
    assert resp.created_at is not None
    assert resp.updated_at is not None

    await _delete_by_id(resp.id)


@pytest.mark.asyncio
async def test_create_template_empty_stages_and_gates(service):
    """create_template works with empty stages and gates."""
    name = _unique_name("svc-create-empty")
    request = TemplateCreateRequest(
        name=name,
        display_name="Empty Stages",
        stages=[],
        gates=[],
    )
    resp = await service.create_template(request)
    assert resp.stages == []
    assert resp.gates == []
    await _delete_by_id(resp.id)


# ── update_template (non-versioned) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_template_display_name(custom_template):
    """update_template updates display_name in-place when versioning is disabled."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    async with async_session_factory() as session:
        svc = TemplateService(session)
        resp = await svc.update_template(
            custom_template["id"],
            TemplateUpdateRequest(display_name="Updated Name"),
        )
    assert resp is not None
    assert resp.display_name == "Updated Name"
    assert resp.id == custom_template["id"]


@pytest.mark.asyncio
async def test_update_template_description(custom_template):
    """update_template updates description in-place."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    async with async_session_factory() as session:
        svc = TemplateService(session)
        resp = await svc.update_template(
            custom_template["id"],
            TemplateUpdateRequest(description="New description"),
        )
    assert resp is not None
    assert resp.description == "New description"


@pytest.mark.asyncio
async def test_update_template_stages(custom_template):
    """update_template updates stages in-place."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    new_stages = [
        StageDefinition(name="parse", agent_role="orchestrator", order=0),
        StageDefinition(name="doc", agent_role="doc", order=1),
    ]
    async with async_session_factory() as session:
        svc = TemplateService(session)
        resp = await svc.update_template(
            custom_template["id"],
            TemplateUpdateRequest(stages=new_stages),
        )
    assert resp is not None
    assert len(resp.stages) == 2
    assert resp.stages[0]["name"] == "parse"
    assert resp.stages[1]["name"] == "doc"


@pytest.mark.asyncio
async def test_update_template_gates(custom_template):
    """update_template updates gates in-place."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    new_gates = [GateDefinition(after_stage="doc", type="human_approve")]
    async with async_session_factory() as session:
        svc = TemplateService(session)
        resp = await svc.update_template(
            custom_template["id"],
            TemplateUpdateRequest(gates=new_gates),
        )
    assert resp is not None
    assert len(resp.gates) == 1
    assert resp.gates[0]["after_stage"] == "doc"


@pytest.mark.asyncio
async def test_update_template_estimated_hours(custom_template):
    """update_template updates estimated_hours in-place."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    async with async_session_factory() as session:
        svc = TemplateService(session)
        resp = await svc.update_template(
            custom_template["id"],
            TemplateUpdateRequest(estimated_hours=7.5),
        )
    assert resp is not None
    assert resp.estimated_hours == 7.5


@pytest.mark.asyncio
async def test_update_template_not_found(service):
    """update_template returns None for a nonexistent template ID."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    resp = await service.update_template(
        "does-not-exist-id",
        TemplateUpdateRequest(display_name="Whatever"),
    )
    assert resp is None


@pytest.mark.asyncio
async def test_update_builtin_template_returns_unchanged(service, builtin_template):
    """update_template returns the unchanged template when is_builtin=True."""
    settings.TEMPLATE_VERSIONING_ENABLED = False
    resp = await service.update_template(
        builtin_template["id"],
        TemplateUpdateRequest(display_name="Should Not Change"),
    )
    assert resp is not None
    assert resp.display_name == "Builtin Template"
    assert resp.is_builtin is True


# ── update_template (versioned) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_template_versioning_creates_new_version():
    """When TEMPLATE_VERSIONING_ENABLED=True, update creates a new version.

    The unique constraint on 'name' prevents two rows sharing the same name in
    the test SQLite DB (as intended for non-versioned use).  We verify the
    versioning branch executes its logic by using a mock session that bypasses
    the DB constraint while still running the real service code.
    """
    from unittest.mock import MagicMock
    original_flag = settings.TEMPLATE_VERSIONING_ENABLED

    # Build a fake "old" template object
    old_id = str(uuid.uuid4())
    fake_template = TaskTemplateModel(
        id=old_id,
        name="ver-test-name",
        display_name="Version 1",
        description="original desc",
        stages=json.dumps([{"name": "code", "agent_role": "coding", "order": 0}]),
        gates=json.dumps([]),
        is_builtin=False,
        version=1,
        is_active=True,
        estimated_hours=2.0,
    )

    captured_new_version = {}

    async def fake_get(model_cls, pk):
        return fake_template

    async def fake_commit():
        pass

    async def fake_refresh(obj):
        # Simulate DB populating created_at/updated_at
        from datetime import datetime
        obj.created_at = datetime(2024, 1, 1)
        obj.updated_at = datetime(2024, 1, 2)
        if obj is not fake_template:
            captured_new_version["obj"] = obj

    mock_session = MagicMock()
    mock_session.get = fake_get
    mock_session.commit = fake_commit
    mock_session.refresh = fake_refresh
    added_objects = []
    mock_session.add = lambda obj: added_objects.append(obj)

    try:
        settings.TEMPLATE_VERSIONING_ENABLED = True
        svc = TemplateService(mock_session)
        resp = await svc.update_template(
            old_id,
            TemplateUpdateRequest(display_name="Version 2"),
        )

        assert resp is not None
        assert resp.display_name == "Version 2"
        assert resp.version == 2
        assert resp.parent_id == old_id
        assert resp.is_active is True

        # Old template should have been deactivated
        assert fake_template.is_active is False

        # A new version object should have been added to the session
        assert len(added_objects) == 1
        new_obj = added_objects[0]
        assert new_obj.version == 2
        assert new_obj.parent_id == old_id
    finally:
        settings.TEMPLATE_VERSIONING_ENABLED = original_flag


@pytest.mark.asyncio
async def test_update_template_versioning_preserves_unchanged_fields():
    """Versioned update preserves fields not passed in the request."""
    from unittest.mock import MagicMock
    original_flag = settings.TEMPLATE_VERSIONING_ENABLED

    old_id = str(uuid.uuid4())
    original_stages = json.dumps([{"name": "code", "agent_role": "coding", "order": 0}])
    original_gates = json.dumps([{"after_stage": "code", "type": "human_approve"}])
    fake_template = TaskTemplateModel(
        id=old_id,
        name="ver-preserve-name",
        display_name="Preserved",
        description="Original desc",
        stages=original_stages,
        gates=original_gates,
        is_builtin=False,
        version=1,
        is_active=True,
        estimated_hours=3.0,
    )

    async def fake_get(model_cls, pk):
        return fake_template

    async def fake_commit():
        pass

    async def fake_refresh(obj):
        from datetime import datetime
        obj.created_at = datetime(2024, 1, 1)
        obj.updated_at = datetime(2024, 1, 2)

    mock_session = MagicMock()
    mock_session.get = fake_get
    mock_session.commit = fake_commit
    mock_session.refresh = fake_refresh
    mock_session.add = lambda obj: None

    try:
        settings.TEMPLATE_VERSIONING_ENABLED = True
        svc = TemplateService(mock_session)
        # Only update display_name; other fields should carry over from old version
        resp = await svc.update_template(
            old_id,
            TemplateUpdateRequest(display_name="Changed"),
        )

        assert resp is not None
        assert resp.display_name == "Changed"
        assert resp.description == "Original desc"
        assert resp.estimated_hours == 3.0
        # stages/gates should be inherited from the original
        assert len(resp.stages) == 1
        assert resp.stages[0]["name"] == "code"
        assert len(resp.gates) == 1
    finally:
        settings.TEMPLATE_VERSIONING_ENABLED = original_flag


# ── delete_template ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_template_non_builtin(service):
    """delete_template removes a non-builtin template and returns True."""
    name = _unique_name("svc-del")
    async with async_session_factory() as session:
        tmpl = TaskTemplateModel(
            name=name,
            display_name="To Delete",
            stages="[]",
            gates="[]",
            is_builtin=False,
        )
        session.add(tmpl)
        await session.commit()
        await session.refresh(tmpl)
        tmpl_id = tmpl.id

    async with async_session_factory() as session:
        svc = TemplateService(session)
        result = await svc.delete_template(tmpl_id)

    assert result is True

    # Confirm gone
    async with async_session_factory() as session:
        obj = await session.get(TaskTemplateModel, tmpl_id)
        assert obj is None


@pytest.mark.asyncio
async def test_delete_template_not_found(service):
    """delete_template returns False for a nonexistent ID."""
    result = await service.delete_template("totally-fake-id-xyz")
    assert result is False


@pytest.mark.asyncio
async def test_delete_template_builtin(service, builtin_template):
    """delete_template returns False and leaves builtin templates intact."""
    result = await service.delete_template(builtin_template["id"])
    assert result is False

    # Template still exists
    async with async_session_factory() as session:
        obj = await session.get(TaskTemplateModel, builtin_template["id"])
        assert obj is not None


# ── list_versions ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_versions_returns_all_versions():
    """list_versions returns all versions of a template ordered desc by version.

    The SQLite test DB enforces UNIQUE on 'name', so we cannot insert multiple
    rows sharing the same name directly.  Instead we use a mock session that
    returns pre-built template objects so we can exercise the ordering logic
    without touching the real DB.
    """
    from unittest.mock import MagicMock, AsyncMock
    from datetime import datetime

    from types import SimpleNamespace

    name = _unique_name("svc-ver")

    def _make_tmpl(v: int):
        """Create a lightweight fake template object compatible with _to_response."""
        return SimpleNamespace(
            id=str(uuid.uuid4()),
            name=name,
            display_name=f"Version {v}",
            description=None,
            stages="[]",
            gates="[]",
            estimated_hours=None,
            is_builtin=False,
            version=v,
            parent_id=None,
            is_active=(v == 3),
            created_at=datetime(2024, 1, v),
            updated_at=datetime(2024, 1, v),
        )

    # Create 3 versions (desc order: 3, 2, 1 — as the service query does)
    templates = [_make_tmpl(v) for v in (3, 2, 1)]

    # Mock the session.execute to return our fake templates
    fake_scalars = MagicMock()
    fake_scalars.all.return_value = templates
    fake_result = MagicMock()
    fake_result.scalars.return_value = fake_scalars

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=fake_result)

    svc = TemplateService(mock_session)
    resp = await svc.list_versions(name)

    assert resp.total == 3
    versions = [item.version for item in resp.items]
    # The service returns whatever order the DB gives — we simulated desc order
    assert versions == [3, 2, 1]


@pytest.mark.asyncio
async def test_list_versions_empty_for_unknown_name(service):
    """list_versions returns an empty list for an unknown template name."""
    resp = await service.list_versions("no-such-template-xyz-abc")
    assert resp.total == 0
    assert resp.items == []


# ── seed_builtin_templates ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seed_builtin_templates_creates_missing():
    """seed_builtin_templates inserts builtin templates that do not yet exist."""
    from app.services.template_service import BUILTIN_TEMPLATES

    # Remove any previously seeded builtin templates
    builtin_names = [t["name"] for t in BUILTIN_TEMPLATES]
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.name.in_(builtin_names))
        )
        for obj in result.scalars().all():
            await session.delete(obj)
        await session.commit()

    # Seed
    async with async_session_factory() as session:
        svc = TemplateService(session)
        await svc.seed_builtin_templates()

    # All builtins should now exist
    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.name.in_(builtin_names))
        )
        created = result.scalars().all()
        created_names = {t.name for t in created}

    assert set(builtin_names) == created_names
    for tmpl in created:
        assert tmpl.is_builtin is True


@pytest.mark.asyncio
async def test_seed_builtin_templates_idempotent():
    """seed_builtin_templates does not duplicate templates on repeated calls."""
    from app.services.template_service import BUILTIN_TEMPLATES

    builtin_names = [t["name"] for t in BUILTIN_TEMPLATES]

    async with async_session_factory() as session:
        svc = TemplateService(session)
        await svc.seed_builtin_templates()

    # Second call should not raise and should not add duplicates
    async with async_session_factory() as session:
        svc = TemplateService(session)
        await svc.seed_builtin_templates()

    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(TaskTemplateModel.name.in_(builtin_names))
        )
        rows = result.scalars().all()
        # Each builtin name appears exactly once (unique constraint on name)
        names_found = [r.name for r in rows]
        assert len(names_found) == len(set(names_found))


@pytest.mark.asyncio
async def test_seed_builtin_templates_includes_github_issue_template():
    """github_issue_template should be seeded with distribution + security stages."""
    async with async_session_factory() as session:
        svc = TemplateService(session)
        await svc.seed_builtin_templates()

    async with async_session_factory() as session:
        result = await session.execute(
            select(TaskTemplateModel).where(
                TaskTemplateModel.name == "github_issue_template"
            )
        )
        template = result.scalar_one_or_none()

    assert template is not None
    assert template.is_builtin is True
    assert template.display_name == "GitHub Issue Template"
    stages = json.loads(template.stages)
    assert stages == [
        {
            "name": "dispatch_issue",
            "agent_role": "issue distribution agent",
            "order": 0,
        },
        {
            "name": "process_security_issue",
            "agent_role": "安全加密agent",
            "order": 1,
        },
    ]


# ── API-level tests for additional coverage ───────────────────────────────────


@pytest.mark.asyncio
async def test_api_create_and_list_template(client):
    """POST then GET list confirms the template is visible."""
    name = _unique_name("api-svc")
    resp = await client.post("/api/v1/templates", json={
        "name": name,
        "display_name": "API Service Test",
        "stages": [{"name": "parse", "agent_role": "orchestrator", "order": 0}],
        "gates": [],
    })
    assert resp.status_code == 201
    tmpl_id = resp.json()["id"]

    list_resp = await client.get("/api/v1/templates")
    assert list_resp.status_code == 200
    ids = [t["id"] for t in list_resp.json()["items"]]
    assert tmpl_id in ids

    await _delete_by_id(tmpl_id)


@pytest.mark.asyncio
async def test_api_list_versions(client):
    """GET /api/v1/templates/by-name/{name}/versions returns versions."""
    name = _unique_name("api-ver")
    resp = await client.post("/api/v1/templates", json={
        "name": name,
        "display_name": "Version Test",
        "stages": [],
        "gates": [],
    })
    assert resp.status_code == 201
    tmpl_id = resp.json()["id"]

    ver_resp = await client.get(f"/api/v1/templates/by-name/{name}/versions")
    assert ver_resp.status_code == 200
    data = ver_resp.json()
    assert data["total"] >= 1
    names = [t["name"] for t in data["items"]]
    assert name in names

    await _delete_by_id(tmpl_id)


@pytest.mark.asyncio
async def test_api_update_template_all_fields(client):
    """PUT /api/v1/templates/{id} updates all mutable fields."""
    name = _unique_name("api-upd")
    create_resp = await client.post("/api/v1/templates", json={
        "name": name,
        "display_name": "Before Update",
        "stages": [],
        "gates": [],
        "estimated_hours": 1.0,
    })
    assert create_resp.status_code == 201
    tmpl_id = create_resp.json()["id"]

    update_payload = {
        "display_name": "After Update",
        "description": "Updated desc",
        "stages": [{"name": "review", "agent_role": "review", "order": 0}],
        "gates": [{"after_stage": "review", "type": "human_approve"}],
        "estimated_hours": 8.0,
    }
    resp = await client.put(f"/api/v1/templates/{tmpl_id}", json=update_payload)
    assert resp.status_code == 200

    data = resp.json()
    assert data["display_name"] == "After Update"
    assert data["description"] == "Updated desc"
    assert len(data["stages"]) == 1
    assert data["stages"][0]["name"] == "review"
    assert len(data["gates"]) == 1
    assert data["estimated_hours"] == 8.0

    await _delete_by_id(tmpl_id)


@pytest.mark.asyncio
async def test_api_update_template_404(client):
    """PUT /api/v1/templates/{id} returns 404 for unknown ID."""
    resp = await client.put("/api/v1/templates/nonexistent-id", json={
        "display_name": "Does not matter",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_delete_template_404(client):
    """DELETE /api/v1/templates/{id} returns 404 for unknown ID."""
    resp = await client.delete("/api/v1/templates/nonexistent-del-id")
    assert resp.status_code == 404
