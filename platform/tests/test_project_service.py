"""Tests for ProjectService — maximise coverage of project_service.py."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.db.session import async_session_factory
from app.models.project import ProjectModel
from app.services.project_service import ProjectService
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest


# ── helpers ──────────────────────────────────────────────────────────────────


def _unique_name(prefix: str = "svc-proj") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _cleanup_project(project_id: str) -> None:
    async with async_session_factory() as session:
        proj = await session.get(ProjectModel, project_id)
        if proj:
            await session.delete(proj)
            await session.commit()


async def _cleanup_by_name_prefix(prefix: str) -> None:
    async with async_session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(ProjectModel).where(ProjectModel.name.like(f"{prefix}%"))
        )
        for obj in result.scalars().all():
            await session.delete(obj)
        await session.commit()


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def service():
    """A ProjectService backed by a live test session."""
    async with async_session_factory() as session:
        yield ProjectService(session)


@pytest_asyncio.fixture
async def created_project():
    """Create a project directly in the DB and clean up after."""
    name = _unique_name("svc-fixture")
    async with async_session_factory() as session:
        proj = ProjectModel(
            name=name,
            display_name=f"Display {name}",
            description="Fixture project",
            repo_url="https://github.com/example/test-repo",
            branch="main",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    yield {"id": proj_id, "name": name}

    await _cleanup_project(proj_id)


# ── list_projects ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_projects_returns_items(created_project):
    """list_projects returns items including the fixture project."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.list_projects()
    ids = [p.id for p in resp.items]
    assert created_project["id"] in ids
    assert resp.total >= 1


@pytest.mark.asyncio
async def test_list_projects_status_filter():
    """list_projects filters by status when status is provided."""
    name_active = _unique_name("svc-status-a")
    name_archived = _unique_name("svc-status-b")
    active_id = archived_id = None
    try:
        async with async_session_factory() as session:
            p_active = ProjectModel(name=name_active, display_name="Active", status="active")
            p_archived = ProjectModel(name=name_archived, display_name="Archived", status="archived")
            session.add(p_active)
            session.add(p_archived)
            await session.commit()
            await session.refresh(p_active)
            await session.refresh(p_archived)
            active_id = p_active.id
            archived_id = p_archived.id

        # Filter by active
        async with async_session_factory() as session:
            svc = ProjectService(session)
            resp = await svc.list_projects(status="active", page_size=200)
        active_ids = {p.id for p in resp.items}
        assert active_id in active_ids
        assert archived_id not in active_ids

        # Filter by archived
        async with async_session_factory() as session:
            svc = ProjectService(session)
            resp = await svc.list_projects(status="archived", page_size=200)
        archived_ids = {p.id for p in resp.items}
        assert archived_id in archived_ids
        assert active_id not in archived_ids
    finally:
        if active_id:
            await _cleanup_project(active_id)
        if archived_id:
            await _cleanup_project(archived_id)


@pytest.mark.asyncio
async def test_list_projects_name_filter_matches_name_and_display_name():
    """list_projects name filter searches both name and display_name columns."""
    base = _unique_name("svc-nf")
    id_match_name = id_match_display = id_nomatch = None
    try:
        async with async_session_factory() as session:
            p1 = ProjectModel(
                name=f"{base}-searchkey-alpha",
                display_name="Nothing special A",
            )
            p2 = ProjectModel(
                name=f"{base}-beta",
                display_name=f"SearchKey Display {base}",
            )
            p3 = ProjectModel(
                name=f"{base}-gamma",
                display_name="Nothing special C",
            )
            for p in (p1, p2, p3):
                session.add(p)
            await session.commit()
            for p in (p1, p2, p3):
                await session.refresh(p)
            id_match_name = p1.id
            id_match_display = p2.id
            id_nomatch = p3.id

        async with async_session_factory() as session:
            svc = ProjectService(session)
            resp = await svc.list_projects(name="searchkey", page_size=200)

        ids = {p.id for p in resp.items}
        assert id_match_name in ids
        assert id_match_display in ids
        assert id_nomatch not in ids
    finally:
        for pid in (id_match_name, id_match_display, id_nomatch):
            if pid:
                await _cleanup_project(pid)


@pytest.mark.asyncio
async def test_list_projects_pagination():
    """list_projects paginates correctly with page and page_size."""
    names = [_unique_name("svc-page") for _ in range(4)]
    ids = []
    try:
        async with async_session_factory() as session:
            for name in names:
                p = ProjectModel(name=name, display_name=f"Display {name}")
                session.add(p)
            await session.commit()
            from sqlalchemy import select as sa_select
            result = await session.execute(
                sa_select(ProjectModel).where(ProjectModel.name.like("svc-page%"))
            )
            ids = [p.id for p in result.scalars().all()]

        async with async_session_factory() as session:
            svc = ProjectService(session)
            page1 = await svc.list_projects(page=1, page_size=2)
            page2 = await svc.list_projects(page=2, page_size=2)

        assert len(page1.items) == 2
        page1_ids = {p.id for p in page1.items}
        page2_ids = {p.id for p in page2.items}
        assert page1_ids.isdisjoint(page2_ids)
    finally:
        for pid in ids:
            await _cleanup_project(pid)


@pytest.mark.asyncio
async def test_list_projects_name_whitespace_stripped():
    """list_projects strips whitespace from the name filter before searching."""
    name = _unique_name("svc-ws")
    proj_id = None
    try:
        async with async_session_factory() as session:
            p = ProjectModel(name=name, display_name="Whitespace Test")
            session.add(p)
            await session.commit()
            await session.refresh(p)
            proj_id = p.id

        async with async_session_factory() as session:
            svc = ProjectService(session)
            # Pass name with surrounding whitespace
            resp = await svc.list_projects(name=f"  {name}  ", page_size=200)

        ids = {p.id for p in resp.items}
        assert proj_id in ids
    finally:
        if proj_id:
            await _cleanup_project(proj_id)


# ── get_project ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_project_found(service, created_project):
    """get_project returns a ProjectResponse for a known ID."""
    resp = await service.get_project(created_project["id"])
    assert resp is not None
    assert resp.id == created_project["id"]
    assert resp.name == created_project["name"]


@pytest.mark.asyncio
async def test_get_project_not_found(service):
    """get_project returns None for an unknown ID."""
    resp = await service.get_project("does-not-exist-xyz")
    assert resp is None


# ── create_project ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_all_fields():
    """create_project persists all fields and returns a valid ProjectResponse."""
    name = _unique_name("svc-create")
    request = ProjectCreateRequest(
        name=name,
        display_name="Full Create",
        repo_url="https://github.com/org/repo",
        repo_local_path="/tmp/local_repo",
        branch="develop",
        description="Full project description",
        sandbox_image="python:3.11",
    )
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.create_project(request)

    assert resp.id is not None
    assert resp.name == name
    assert resp.display_name == "Full Create"
    assert resp.repo_url == "https://github.com/org/repo"
    assert resp.repo_local_path == "/tmp/local_repo"
    assert resp.branch == "develop"
    assert resp.description == "Full project description"
    assert resp.sandbox_image == "python:3.11"
    assert resp.status == "active"
    assert resp.created_at is not None
    assert resp.updated_at is not None

    await _cleanup_project(resp.id)


@pytest.mark.asyncio
async def test_create_project_minimal():
    """create_project works with only required fields."""
    name = _unique_name("svc-create-min")
    request = ProjectCreateRequest(name=name, display_name="Minimal")
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.create_project(request)

    assert resp.id is not None
    assert resp.repo_url is None
    assert resp.branch == "main"

    await _cleanup_project(resp.id)


# ── update_project ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_project_display_name(created_project):
    """update_project updates display_name."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(display_name="Updated Name"),
        )
    assert resp is not None
    assert resp.display_name == "Updated Name"
    assert resp.id == created_project["id"]


@pytest.mark.asyncio
async def test_update_project_repo_url(created_project):
    """update_project updates repo_url."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(repo_url="https://github.com/new/repo"),
        )
    assert resp is not None
    assert resp.repo_url == "https://github.com/new/repo"


@pytest.mark.asyncio
async def test_update_project_branch(created_project):
    """update_project updates branch."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(branch="feature-branch"),
        )
    assert resp is not None
    assert resp.branch == "feature-branch"


@pytest.mark.asyncio
async def test_update_project_description(created_project):
    """update_project updates description."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(description="New description"),
        )
    assert resp is not None
    assert resp.description == "New description"


@pytest.mark.asyncio
async def test_update_project_status(created_project):
    """update_project updates status."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(status="archived"),
        )
    assert resp is not None
    assert resp.status == "archived"


@pytest.mark.asyncio
async def test_update_project_repo_local_path(created_project):
    """update_project updates repo_local_path."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(repo_local_path="/workspace/myrepo"),
        )
    assert resp is not None
    assert resp.repo_local_path == "/workspace/myrepo"


@pytest.mark.asyncio
async def test_update_project_sandbox_image(created_project):
    """update_project updates sandbox_image."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(sandbox_image="node:20"),
        )
    assert resp is not None
    assert resp.sandbox_image == "node:20"


@pytest.mark.asyncio
async def test_update_project_all_fields(created_project):
    """update_project updates all mutable fields at once."""
    async with async_session_factory() as session:
        svc = ProjectService(session)
        resp = await svc.update_project(
            created_project["id"],
            ProjectUpdateRequest(
                display_name="All Fields Updated",
                repo_url="https://github.com/all/fields",
                branch="all-fields-branch",
                description="All fields updated desc",
                status="archived",
                repo_local_path="/all/fields/path",
                sandbox_image="ubuntu:22.04",
            ),
        )
    assert resp is not None
    assert resp.display_name == "All Fields Updated"
    assert resp.repo_url == "https://github.com/all/fields"
    assert resp.branch == "all-fields-branch"
    assert resp.description == "All fields updated desc"
    assert resp.status == "archived"
    assert resp.repo_local_path == "/all/fields/path"
    assert resp.sandbox_image == "ubuntu:22.04"


@pytest.mark.asyncio
async def test_update_project_not_found(service):
    """update_project returns None when the project does not exist."""
    resp = await service.update_project(
        "nonexistent-id-xyz",
        ProjectUpdateRequest(display_name="Whatever"),
    )
    assert resp is None


# ── delete_project ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_project_success():
    """delete_project removes the project and returns True."""
    name = _unique_name("svc-del")
    async with async_session_factory() as session:
        proj = ProjectModel(name=name, display_name="To Delete")
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    async with async_session_factory() as session:
        svc = ProjectService(session)
        result = await svc.delete_project(proj_id)

    assert result is True

    # Confirm it's gone
    async with async_session_factory() as session:
        obj = await session.get(ProjectModel, proj_id)
        assert obj is None


@pytest.mark.asyncio
async def test_delete_project_not_found(service):
    """delete_project returns False when the project does not exist."""
    result = await service.delete_project("totally-fake-proj-id")
    assert result is False


# ── sync_repo ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_repo_not_found(service):
    """sync_repo returns None when the project does not exist."""
    result = await service.sync_repo("nonexistent-proj-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_sync_repo_no_repo_url():
    """sync_repo raises ValueError when the project has no repo_url."""
    name = _unique_name("svc-sync-nourl")
    async with async_session_factory() as session:
        proj = ProjectModel(name=name, display_name="No URL", repo_url=None)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    try:
        async with async_session_factory() as session:
            svc = ProjectService(session)
            with pytest.raises(ValueError, match="no repo_url"):
                await svc.sync_repo(proj_id)
    finally:
        await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_sync_repo_success():
    """sync_repo calls analyze_repo and persists tech_stack/tree/last_synced_at."""
    name = _unique_name("svc-sync-ok")
    async with async_session_factory() as session:
        proj = ProjectModel(
            name=name,
            display_name="Sync OK",
            repo_url="https://github.com/example/testrepo",
            branch="main",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    fake_ctx = SimpleNamespace(
        tech_stack=["Python", "FastAPI"],
        tree="platform/\n  app/",
        readme_summary="A test repository for testing purposes.",
    )

    try:
        with patch(
            "app.services.repo_analyzer.analyze_repo",
            new=AsyncMock(return_value=fake_ctx),
        ):
            async with async_session_factory() as session:
                svc = ProjectService(session)
                resp = await svc.sync_repo(proj_id)

        assert resp is not None
        assert resp.tech_stack == ["Python", "FastAPI"]
        assert resp.tree_depth == 2
        assert resp.readme_length == len(fake_ctx.readme_summary)
        assert resp.synced_at is not None

        # Verify DB fields updated
        async with async_session_factory() as session:
            proj_db = await session.get(ProjectModel, proj_id)
            assert proj_db.tech_stack == ["Python", "FastAPI"]
            assert proj_db.repo_tree == "platform/\n  app/"
            assert proj_db.last_synced_at is not None
    finally:
        await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_sync_repo_not_found_error():
    """sync_repo raises ValueError when analyze_repo raises RepoNotFoundError."""
    name = _unique_name("svc-sync-404")
    async with async_session_factory() as session:
        proj = ProjectModel(
            name=name,
            display_name="Sync 404",
            repo_url="https://github.com/nonexistent/repo",
            branch="main",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        proj_id = proj.id

    try:
        from app.services.repo_analyzer import RepoNotFoundError

        with patch(
            "app.services.repo_analyzer.analyze_repo",
            new=AsyncMock(side_effect=RepoNotFoundError("Repo not found")),
        ):
            async with async_session_factory() as session:
                svc = ProjectService(session)
                with pytest.raises(ValueError, match="Repo not found"):
                    await svc.sync_repo(proj_id)
    finally:
        await _cleanup_project(proj_id)


# ── API-level tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_create_project(client):
    """POST /api/v1/projects creates a project and returns 201."""
    name = _unique_name("api-create")
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": "API Create Test",
        "repo_url": "https://github.com/test/repo",
        "branch": "main",
        "description": "API test",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == name
    assert data["status"] == "active"
    await _cleanup_project(data["id"])


@pytest.mark.asyncio
async def test_api_create_project_duplicate_409(client):
    """POST /api/v1/projects returns 409 for duplicate name."""
    name = _unique_name("api-dup")
    resp1 = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "First",
    })
    assert resp1.status_code == 201
    proj_id = resp1.json()["id"]

    resp2 = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "Second",
    })
    assert resp2.status_code == 409

    await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_api_list_projects_status_filter(client):
    """GET /api/v1/projects?status=archived returns only archived projects."""
    name = _unique_name("api-status")
    resp = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "Status Filter Test",
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    # Archive it
    await client.put(f"/api/v1/projects/{proj_id}", json={"status": "archived"})

    list_resp = await client.get("/api/v1/projects", params={"status": "archived", "page_size": 200})
    assert list_resp.status_code == 200
    data = list_resp.json()
    ids = {p["id"] for p in data["items"]}
    assert proj_id in ids

    await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_api_get_project(client):
    """GET /api/v1/projects/{id} returns the project."""
    name = _unique_name("api-get")
    resp = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "Get Test",
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    get_resp = await client.get(f"/api/v1/projects/{proj_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == proj_id
    assert data["name"] == name

    await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_api_update_project(client):
    """PUT /api/v1/projects/{id} updates all mutable fields."""
    name = _unique_name("api-upd")
    resp = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "Before Update",
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    upd_resp = await client.put(f"/api/v1/projects/{proj_id}", json={
        "display_name": "After Update",
        "description": "Updated description",
        "status": "archived",
        "branch": "feature",
        "repo_url": "https://github.com/updated/repo",
        "repo_local_path": "/updated/path",
        "sandbox_image": "node:18",
    })
    assert upd_resp.status_code == 200
    data = upd_resp.json()
    assert data["display_name"] == "After Update"
    assert data["description"] == "Updated description"
    assert data["status"] == "archived"
    assert data["branch"] == "feature"
    assert data["repo_url"] == "https://github.com/updated/repo"
    assert data["repo_local_path"] == "/updated/path"
    assert data["sandbox_image"] == "node:18"

    await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_api_update_project_404(client):
    """PUT /api/v1/projects/nonexistent returns 404."""
    resp = await client.put("/api/v1/projects/nonexistent-id", json={
        "display_name": "Does not matter",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_sync_project_not_found(client):
    """POST /api/v1/projects/nonexistent/sync returns 404."""
    resp = await client.post("/api/v1/projects/nonexistent-id/sync")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_sync_project_no_repo_url(client):
    """POST /api/v1/projects/{id}/sync returns 400 when project has no repo_url."""
    name = _unique_name("api-sync-nourl")
    resp = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "No URL Project",
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    sync_resp = await client.post(f"/api/v1/projects/{proj_id}/sync")
    assert sync_resp.status_code == 400
    assert "repo_url" in sync_resp.json()["detail"]

    await _cleanup_project(proj_id)


@pytest.mark.asyncio
async def test_api_delete_project(client):
    """DELETE /api/v1/projects/{id} returns 204 and removes the project."""
    name = _unique_name("api-del")
    resp = await client.post("/api/v1/projects", json={
        "name": name, "display_name": "To Delete",
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/projects/{proj_id}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/api/v1/projects/{proj_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_api_delete_project_404(client):
    """DELETE /api/v1/projects/nonexistent returns 404."""
    resp = await client.delete("/api/v1/projects/nonexistent-xyz")
    assert resp.status_code == 404
