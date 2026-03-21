"""Tests for project-level webhook endpoints (GitHub, Jira, GitLab)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from uuid import uuid4

from app.db.session import async_session_factory
from app.models.project import ProjectModel
from app.models.integration import ProjectIntegrationModel
from app.models.task import TaskModel
from app.models.trigger import TriggerRuleModel, TriggerEventModel
from sqlalchemy import delete


def _unique_name() -> str:
    return f"wh-test-{uuid4().hex[:12]}"


@pytest_asyncio.fixture
async def project_with_github(client):
    """Create project with GitHub integration and a trigger rule."""
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": f"Display {name}",
    })
    assert resp.status_code == 201
    project = resp.json()
    pid = project["id"]

    # Create GitHub integration
    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "github",
    })
    assert resp.status_code == 201
    integration = resp.json()

    # Create a trigger rule for this project
    async with async_session_factory() as session:
        rule = TriggerRuleModel(
            name="test-github-rule",
            source="github",
            event_type="pr_opened",
            project_id=pid,
            title_template="PR: {pr_title}",
            enabled=True,
        )
        session.add(rule)
        await session.commit()
        rule_id = rule.id

    yield {
        "project": project,
        "integration": integration,
        "rule_id": rule_id,
    }

    # Cleanup
    async with async_session_factory() as session:
        await session.execute(
            delete(TriggerEventModel).where(TriggerEventModel.project_id == pid)
        )
        await session.execute(
            delete(TriggerRuleModel).where(TriggerRuleModel.project_id == pid)
        )
        await session.execute(
            delete(ProjectIntegrationModel).where(
                ProjectIntegrationModel.project_id == pid
            )
        )
        proj = await session.get(ProjectModel, pid)
        if proj:
            await session.delete(proj)
        await session.commit()


def _github_signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── GitHub project-level webhook ──────────────────────────────


@pytest.mark.asyncio
async def test_github_project_webhook_success(client, project_with_github):
    pid = project_with_github["project"]["id"]
    secret = project_with_github["integration"]["webhook_secret"]

    payload = {
        "action": "opened",
        "pull_request": {
            "number": 42,
            "title": "Add feature X",
            "html_url": "https://github.com/org/repo/pull/42",
            "user": {"login": "alice"},
            "head": {"ref": "feature-x"},
            "base": {"ref": "main"},
            "labels": [],
        },
        "repository": {"name": "repo", "full_name": "org/repo"},
        "sender": {"login": "alice"},
    }
    body = json.dumps(payload).encode()
    signature = _github_signature(body, secret)

    resp = await client.post(
        f"/webhooks/github/{pid}",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["event"] == "pr_opened"
    assert data["project_id"] == pid


@pytest.mark.asyncio
async def test_github_project_webhook_bad_signature(client, project_with_github):
    pid = project_with_github["project"]["id"]

    payload = {"action": "opened", "pull_request": {}, "repository": {}, "sender": {}}
    body = json.dumps(payload).encode()

    resp = await client.post(
        f"/webhooks/github/{pid}",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_github_project_webhook_no_integration(client):
    """Webhook to a project without integration → 404."""
    resp = await client.post(
        "/webhooks/github/nonexistent-project",
        content=b'{"action":"opened"}',
        headers={
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_github_issue_project_webhook_persists_issue_metadata(client):
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": f"Display {name}",
    })
    assert resp.status_code == 201
    project = resp.json()
    pid = project["id"]

    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "github",
    })
    assert resp.status_code == 201
    integration = resp.json()
    secret = integration["webhook_secret"]

    async with async_session_factory() as session:
        rule = TriggerRuleModel(
            name="test-github-issue-rule",
            source="github",
            event_type="issue_opened",
            project_id=pid,
            title_template="Issue: {issue_title}",
            template_id=None,
            enabled=True,
        )
        session.add(rule)
        await session.commit()

    payload = {
        "action": "opened",
        "issue": {
            "number": 13,
            "title": "安全加密",
            "body": "安全加密agent，对本项目的phone字段进行安全加密",
            "html_url": "https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
            "user": {"login": "jowang"},
            "labels": [],
        },
        "repository": {"name": "starbucks-asg-api", "full_name": "china/starbucks-asg-api"},
        "sender": {"login": "jowang"},
    }
    body = json.dumps(payload).encode()
    signature = _github_signature(body, secret)

    resp = await client.post(
        f"/webhooks/github/{pid}",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    assert task_id is not None

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    task_data = task_resp.json()
    assert task_data["github_issue_number"] == 13
    assert "china/starbucks-asg-api" in (task_data["description"] or "")
    assert "https://scm.starbucks.com/china/starbucks-asg-api/issues/13" in (task_data["description"] or "")
    assert "phone字段进行安全加密" in (task_data["description"] or "")

    async with async_session_factory() as session:
        await session.execute(
            delete(TriggerEventModel).where(TriggerEventModel.project_id == pid)
        )
        await session.execute(
            delete(TriggerRuleModel).where(TriggerRuleModel.project_id == pid)
        )
        await session.execute(
            delete(ProjectIntegrationModel).where(
                ProjectIntegrationModel.project_id == pid
            )
        )
        task = await session.get(TaskModel, task_id)
        if task:
            await session.delete(task)
        proj = await session.get(ProjectModel, pid)
        if proj:
            await session.delete(proj)
        await session.commit()


# ── Jira project-level webhook ────────────────────────────────


@pytest_asyncio.fixture
async def project_with_jira(client):
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": f"Display {name}",
    })
    assert resp.status_code == 201
    project = resp.json()
    pid = project["id"]

    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "jira",
    })
    assert resp.status_code == 201
    integration = resp.json()

    yield {"project": project, "integration": integration}

    async with async_session_factory() as session:
        await session.execute(
            delete(TriggerEventModel).where(TriggerEventModel.project_id == pid)
        )
        await session.execute(
            delete(ProjectIntegrationModel).where(
                ProjectIntegrationModel.project_id == pid
            )
        )
        proj = await session.get(ProjectModel, pid)
        if proj:
            await session.delete(proj)
        await session.commit()


@pytest.mark.asyncio
async def test_jira_project_webhook_success(client, project_with_jira):
    pid = project_with_jira["project"]["id"]
    secret = project_with_jira["integration"]["webhook_secret"]

    payload = {
        "webhookEvent": "jira:issue_created",
        "issue": {"key": "PROJ-1", "fields": {"summary": "Test issue", "labels": []}},
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    resp = await client.post(
        f"/webhooks/jira/{pid}",
        content=body,
        headers={
            "X-Atlassian-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["event"] == "issue_created"
    assert data["project_id"] == pid


@pytest.mark.asyncio
async def test_jira_project_webhook_bad_signature(client, project_with_jira):
    pid = project_with_jira["project"]["id"]
    resp = await client.post(
        f"/webhooks/jira/{pid}",
        content=b'{"webhookEvent":"jira:issue_created"}',
        headers={
            "X-Atlassian-Signature": "invalidsig",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 403


# ── GitLab project-level webhook ──────────────────────────────


@pytest_asyncio.fixture
async def project_with_gitlab(client):
    name = _unique_name()
    resp = await client.post("/api/v1/projects", json={
        "name": name,
        "display_name": f"Display {name}",
    })
    assert resp.status_code == 201
    project = resp.json()
    pid = project["id"]

    resp = await client.post(f"/api/v1/projects/{pid}/integrations", json={
        "provider": "gitlab",
    })
    assert resp.status_code == 201
    integration = resp.json()

    yield {"project": project, "integration": integration}

    async with async_session_factory() as session:
        await session.execute(
            delete(TriggerEventModel).where(TriggerEventModel.project_id == pid)
        )
        await session.execute(
            delete(ProjectIntegrationModel).where(
                ProjectIntegrationModel.project_id == pid
            )
        )
        proj = await session.get(ProjectModel, pid)
        if proj:
            await session.delete(proj)
        await session.commit()


@pytest.mark.asyncio
async def test_gitlab_project_webhook_success(client, project_with_gitlab):
    pid = project_with_gitlab["project"]["id"]
    secret = project_with_gitlab["integration"]["webhook_secret"]

    payload = {
        "object_kind": "merge_request",
        "object_attributes": {"action": "open", "iid": 1, "title": "MR title"},
        "project": {"name": "myproject"},
        "user": {"username": "bob"},
    }

    resp = await client.post(
        f"/webhooks/gitlab/{pid}",
        json=payload,
        headers={"X-Gitlab-Token": secret, "X-Gitlab-Event": "Merge Request Hook"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["event"] == "mr_open"
    assert data["project_id"] == pid


@pytest.mark.asyncio
async def test_gitlab_project_webhook_bad_token(client, project_with_gitlab):
    pid = project_with_gitlab["project"]["id"]
    resp = await client.post(
        f"/webhooks/gitlab/{pid}",
        json={"object_kind": "push"},
        headers={"X-Gitlab-Token": "wrong-token"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_gitlab_project_webhook_no_integration(client):
    resp = await client.post(
        "/webhooks/gitlab/nonexistent-project",
        json={"object_kind": "push"},
        headers={"X-Gitlab-Token": "any"},
    )
    assert resp.status_code == 404
