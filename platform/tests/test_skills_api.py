"""Tests for the Skills API endpoints."""
import pytest
import pytest_asyncio
from uuid import uuid4

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.skill import SkillModel, SkillVersionModel


def _unique_name(prefix: str = "skill-test") -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


@pytest_asyncio.fixture
async def skill_factory():
    """Factory that creates skills via the DB and tracks them for cleanup."""
    created_names: list[str] = []

    async def _create(**overrides) -> dict:
        defaults = {
            "name": _unique_name(),
            "display_name": "Test Skill",
            "layer": "L1",
        }
        defaults.update(overrides)
        created_names.append(defaults["name"])
        async with async_session_factory() as session:
            skill = SkillModel(**defaults)
            session.add(skill)
            await session.commit()
            await session.refresh(skill)
            return {
                "id": skill.id,
                "name": skill.name,
                "display_name": skill.display_name,
                "layer": skill.layer,
                "status": skill.status,
                "version": skill.version,
            }

    yield _create

    # Cleanup all skills (and cascade to versions)
    async with async_session_factory() as session:
        for name in created_names:
            result = await session.execute(
                select(SkillModel).where(SkillModel.name == name)
            )
            skill = result.scalar_one_or_none()
            if skill:
                # Delete version snapshots first
                ver_result = await session.execute(
                    select(SkillVersionModel).where(SkillVersionModel.skill_id == skill.id)
                )
                for v in ver_result.scalars().all():
                    await session.delete(v)
                await session.delete(skill)
        await session.commit()


# ── Create ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_skill(client):
    """POST /api/v1/skills creates a skill and returns 201."""
    name = _unique_name()
    payload = {
        "name": name,
        "display_name": "My New Skill",
        "layer": "L1",
        "description": "A test skill",
        "tags": ["backend"],
        "applicable_roles": ["coding"],
        "content": "print('hello')",
        "git_path": "skills/test.py",
    }
    resp = await client.post("/api/v1/skills", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == name
    assert data["display_name"] == "My New Skill"
    assert data["layer"] == "L1"
    assert data["status"] == "active"
    assert data["version"] == "1.0.0"
    assert data["description"] == "A test skill"
    assert data["tags"] == ["backend"]
    assert data["applicable_roles"] == ["coding"]
    assert data["content"] == "print('hello')"
    assert data["git_path"] == "skills/test.py"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data

    # Cleanup
    async with async_session_factory() as session:
        result = await session.execute(select(SkillModel).where(SkillModel.name == name))
        skill = result.scalar_one_or_none()
        if skill:
            await session.delete(skill)
            await session.commit()


# ── Get / 404 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_skill(client, skill_factory):
    """GET /api/v1/skills/{name} returns the created skill."""
    info = await skill_factory(display_name="Fetch Me", description="desc")
    resp = await client.get(f"/api/v1/skills/{info['name']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == info["name"]
    assert data["display_name"] == "Fetch Me"
    assert data["id"] == info["id"]
    assert data["status"] == "active"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_get_skill_404(client):
    """GET /api/v1/skills/{name} returns 404 for unknown skill."""
    resp = await client.get("/api/v1/skills/nonexistent-skill-xyz")
    assert resp.status_code == 404


# ── List / Filter ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_skills(client, skill_factory):
    """GET /api/v1/skills returns a paginated list of skills."""
    await skill_factory(display_name="Skill A")
    await skill_factory(display_name="Skill B")

    resp = await client.get("/api/v1/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


@pytest.mark.asyncio
async def test_list_skills_filter_layer(client, skill_factory):
    """GET /api/v1/skills?layer=L1 filters by layer."""
    await skill_factory(layer="L1")
    await skill_factory(layer="L2")

    resp_l1 = await client.get("/api/v1/skills", params={"layer": "L1"})
    assert resp_l1.status_code == 200
    data_l1 = resp_l1.json()
    assert all(s["layer"] == "L1" for s in data_l1["items"])

    resp_l2 = await client.get("/api/v1/skills", params={"layer": "L2"})
    assert resp_l2.status_code == 200
    data_l2 = resp_l2.json()
    assert all(s["layer"] == "L2" for s in data_l2["items"])


@pytest.mark.asyncio
async def test_list_skills_filter_tag(client, skill_factory):
    """GET /api/v1/skills?tag=... filters by tag token."""
    tagged = await skill_factory(tags=["domain", "redemption"])
    await skill_factory(tags=["security"])

    resp = await client.get("/api/v1/skills", params={"tag": "redemption"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    names = [s["name"] for s in items]
    assert tagged["name"] in names
    assert all("redemption" in (s["tags"] or []) for s in items)


@pytest.mark.asyncio
async def test_list_skills_filter_role(client, skill_factory):
    """GET /api/v1/skills?role=... filters by applicable role."""
    role_match = await skill_factory(applicable_roles=["coding", "review"])
    await skill_factory(applicable_roles=["doc"])

    resp = await client.get("/api/v1/skills", params={"role": "review"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    names = [s["name"] for s in items]
    assert role_match["name"] in names
    assert all("review" in (s["applicable_roles"] or []) for s in items)


@pytest.mark.asyncio
async def test_list_skills_filter_status(client, skill_factory):
    """GET /api/v1/skills?status=... includes requested status."""
    active = await skill_factory()
    archived = await skill_factory()

    # Archive one skill through API to match real behavior.
    archive_resp = await client.delete(f"/api/v1/skills/{archived['name']}")
    assert archive_resp.status_code == 200

    archived_resp = await client.get("/api/v1/skills", params={"status": "archived"})
    assert archived_resp.status_code == 200
    archived_names = [s["name"] for s in archived_resp.json()["items"]]
    assert archived["name"] in archived_names
    assert active["name"] not in archived_names

    all_resp = await client.get("/api/v1/skills", params={"status": "all"})
    assert all_resp.status_code == 200
    all_names = [s["name"] for s in all_resp.json()["items"]]
    assert archived["name"] in all_names
    assert active["name"] in all_names


# ── Update (version snapshot) ─────────────────────────


@pytest.mark.asyncio
async def test_update_skill_creates_version(client, skill_factory):
    """PUT /api/v1/skills/{name} creates a version snapshot."""
    info = await skill_factory(content="v1 content")
    name = info["name"]

    resp = await client.put(f"/api/v1/skills/{name}", json={
        "version": "2.0.0",
        "content": "v2 content",
        "display_name": "Updated Skill",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "2.0.0"
    assert data["content"] == "v2 content"
    assert data["display_name"] == "Updated Skill"

    # Verify a version snapshot was created for the old version
    ver_resp = await client.get(f"/api/v1/skills/{name}/versions")
    assert ver_resp.status_code == 200
    versions = ver_resp.json()["versions"]
    assert len(versions) >= 2
    old_versions = [v for v in versions if v["version"] == "1.0.0"]
    assert len(old_versions) >= 1


# ── Archive ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_skill(client, skill_factory):
    """DELETE /api/v1/skills/{name} archives the skill."""
    info = await skill_factory()
    name = info["name"]

    resp = await client.delete(f"/api/v1/skills/{name}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "archived"
    assert data["name"] == name

    # Default list now includes all statuses (status filter is explicit).
    list_resp = await client.get("/api/v1/skills")
    list_names = [s["name"] for s in list_resp.json()["items"]]
    assert name in list_names

    # Active-only query should exclude archived skills.
    active_resp = await client.get("/api/v1/skills", params={"status": "active"})
    active_names = [s["name"] for s in active_resp.json()["items"]]
    assert name not in active_names


# ── Versions ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_versions(client, skill_factory):
    """GET /api/v1/skills/{name}/versions returns version history."""
    info = await skill_factory(content="original")
    name = info["name"]

    # Update to create a version snapshot
    await client.put(f"/api/v1/skills/{name}", json={
        "version": "2.0.0",
        "content": "updated",
    })

    resp = await client.get(f"/api/v1/skills/{name}/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == name
    versions = data["versions"]
    assert len(versions) >= 2

    # First entry should be the current version
    assert versions[0]["current"] is True
    assert versions[0]["version"] == "2.0.0"

    # Second entry should be the snapshot of the old version
    assert versions[1]["current"] is False
    assert versions[1]["version"] == "1.0.0"


# ── Rollback ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_skill(client, skill_factory):
    """POST /api/v1/skills/{name}/rollback?version=1.0.0 restores old version."""
    info = await skill_factory(content="original content")
    name = info["name"]

    # Update to v2
    await client.put(f"/api/v1/skills/{name}", json={
        "version": "2.0.0",
        "content": "new content",
    })

    # Rollback to v1
    resp = await client.post(f"/api/v1/skills/{name}/rollback", params={"version": "1.0.0"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0.0"
    assert data["content"] == "original content"


# ── Stats ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_stats(client, skill_factory):
    """GET /api/v1/skills/stats returns aggregate counts by layer and status."""
    await skill_factory(layer="L1")
    await skill_factory(layer="L2")

    resp = await client.get("/api/v1/skills/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "by_layer" in data
    assert "by_status" in data
    assert data["total"] >= 2
    assert "L1" in data["by_layer"]
    assert "L2" in data["by_layer"]
    assert data["by_layer"]["L1"] >= 1
    assert data["by_layer"]["L2"] >= 1
    assert "active" in data["by_status"]
