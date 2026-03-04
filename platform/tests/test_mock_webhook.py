"""Tests for POST /api/v1/projects/{project_id}/mock-webhook endpoint."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.project import ProjectModel
from app.models.task import TaskModel
from app.models.trigger import TriggerEventModel, TriggerRuleModel


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_project():
    pid = "tt-mock-proj-1"
    async with async_session_factory() as session:
        proj = ProjectModel(id=pid, name="mock-webhook-test", display_name="Mock Test")
        session.add(proj)
        await session.commit()
    yield pid
    async with async_session_factory() as session:
        # Clean up tasks created during tests
        result = await session.execute(
            select(TaskModel).where(TaskModel.project_id == pid)
        )
        for task in result.scalars().all():
            await session.delete(task)
        # Clean up trigger events
        result = await session.execute(
            select(TriggerEventModel).where(TriggerEventModel.project_id == pid)
        )
        for ev in result.scalars().all():
            await session.delete(ev)
        proj = await session.get(ProjectModel, pid)
        if proj:
            await session.delete(proj)
        await session.commit()


@pytest_asyncio.fixture
async def seed_rule(seed_project):
    """创建一条绑定到测试项目的 GitHub issue 触发规则。"""
    rule_id = "tt-mock-rule-1"
    async with async_session_factory() as session:
        rule = TriggerRuleModel(
            id=rule_id,
            name="mock github issue rule",
            source="github",
            event_type="issues.opened",
            filters=None,
            title_template="Issue #{number}: {title}",
            desc_template="Body: {issue.body}",
            project_id=seed_project,
            enabled=True,
        )
        session.add(rule)
        await session.commit()
    yield rule_id
    async with async_session_factory() as session:
        r = await session.get(TriggerRuleModel, rule_id)
        if r:
            await session.delete(r)
            await session.commit()


@pytest_asyncio.fixture
async def seed_rule_with_filter(seed_project):
    """带 label 过滤器的规则。"""
    rule_id = "tt-mock-rule-2"
    async with async_session_factory() as session:
        rule = TriggerRuleModel(
            id=rule_id,
            name="filtered github rule",
            source="github",
            event_type="issues.opened",
            filters={
                "op": "and",
                "conditions": [
                    {"type": "labels", "value": ["auto-agent"]},
                ],
            },
            title_template="Filtered: {title}",
            project_id=seed_project,
            enabled=True,
        )
        session.add(rule)
        await session.commit()
    yield rule_id
    async with async_session_factory() as session:
        r = await session.get(TriggerRuleModel, rule_id)
        if r:
            await session.delete(r)
            await session.commit()


# ── Dry-Run Tests ─────────────────────────────────────────────────────────────


class TestMockWebhookDryRun:
    """dry_run=true 不创建任务，仅预览。"""

    @pytest.mark.asyncio
    async def test_dry_run_matched(self, client, seed_rule, seed_project):
        resp = await client.post(
            f"/api/v1/projects/{seed_project}/mock-webhook",
            json={
                "source": "github",
                "event_type": "issues.opened",
                "title": "Test issue",
                "number": 42,
                "dry_run": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["matched"] is True
        assert data["result"] == "would_trigger"
        assert data["task_id"] is None
        assert "42" in (data.get("rendered_title") or "")

    @pytest.mark.asyncio
    async def test_dry_run_no_rule(self, client, seed_project):
        resp = await client.post(
            f"/api/v1/projects/{seed_project}/mock-webhook",
            json={
                "source": "github",
                "event_type": "push",
                "title": "some commit",
                "dry_run": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["matched"] is False
        assert data["result"] == "skipped_no_rule"

    @pytest.mark.asyncio
    async def test_dry_run_filter_skip(self, client, seed_rule_with_filter, seed_project):
        resp = await client.post(
            f"/api/v1/projects/{seed_project}/mock-webhook",
            json={
                "source": "github",
                "event_type": "issues.opened",
                "title": "No matching labels",
                "labels": ["bug", "low"],
                "dry_run": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] in ("skipped_filter", "skipped_no_rule")


# ── Actual Trigger Tests ──────────────────────────────────────────────────────


class TestMockWebhookTrigger:
    """dry_run=false 实际创建任务。"""

    @pytest.mark.asyncio
    async def test_trigger_creates_task(self, client, seed_rule, seed_project):
        resp = await client.post(
            f"/api/v1/projects/{seed_project}/mock-webhook",
            json={
                "source": "github",
                "event_type": "issues.opened",
                "title": "Implement feature X",
                "number": 99,
                "author": "alice",
                "dry_run": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
        assert data["matched"] is True
        assert data["result"] == "triggered"
        assert data["task_id"] is not None

        # Verify task was created with github_issue_number
        task_resp = await client.get(f"/api/v1/tasks/{data['task_id']}")
        assert task_resp.status_code == 200
        task_data = task_resp.json()
        assert task_data["github_issue_number"] == 99

    @pytest.mark.asyncio
    async def test_trigger_no_rule(self, client, seed_project):
        resp = await client.post(
            f"/api/v1/projects/{seed_project}/mock-webhook",
            json={
                "source": "webhook",
                "event_type": "custom",
                "title": "No rule exists",
                "dry_run": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is False
        assert data["task_id"] is None

    @pytest.mark.asyncio
    async def test_trigger_without_number(self, client, seed_rule, seed_project):
        resp = await client.post(
            f"/api/v1/projects/{seed_project}/mock-webhook",
            json={
                "source": "github",
                "event_type": "issues.opened",
                "title": "Issue without number",
                "dry_run": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["task_id"] is not None


# ── Payload Construction Tests ────────────────────────────────────────────────


class TestBuildNormalizedPayload:
    """Test _build_normalized_payload for different sources."""

    def test_github_issue_payload(self):
        from app.schemas.trigger import MockWebhookRequest
        from app.services.trigger_service import _build_normalized_payload

        req = MockWebhookRequest(
            source="github",
            event_type="issues.opened",
            title="Test issue",
            body="Some body",
            number=42,
            author="alice",
            labels=["bug", "urgent"],
        )
        payload = _build_normalized_payload(req)
        assert payload["issue"]["title"] == "Test issue"
        assert payload["issue"]["body"] == "Some body"
        assert payload["issue"]["number"] == 42
        assert payload["issue"]["user"]["login"] == "alice"
        assert len(payload["issue"]["labels"]) == 2
        assert payload["number"] == 42

    def test_github_push_payload(self):
        from app.schemas.trigger import MockWebhookRequest
        from app.services.trigger_service import _build_normalized_payload

        req = MockWebhookRequest(
            source="github",
            event_type="push",
            title="fix: typo",
            ref="refs/heads/main",
        )
        payload = _build_normalized_payload(req)
        assert payload["ref"] == "refs/heads/main"
        assert payload["head_commit"]["message"] == "fix: typo"

    def test_github_pr_payload(self):
        from app.schemas.trigger import MockWebhookRequest
        from app.services.trigger_service import _build_normalized_payload

        req = MockWebhookRequest(
            source="github",
            event_type="pull_request.opened",
            title="Add feature",
            number=10,
            ref="refs/heads/feature",
        )
        payload = _build_normalized_payload(req)
        assert "pull_request" in payload
        assert payload["pull_request"]["title"] == "Add feature"
        assert payload["pull_request"]["head"]["ref"] == "refs/heads/feature"

    def test_gitlab_payload(self):
        from app.schemas.trigger import MockWebhookRequest
        from app.services.trigger_service import _build_normalized_payload

        req = MockWebhookRequest(
            source="gitlab",
            event_type="issue.open",
            title="GL Issue",
            author="bob",
            ref="refs/heads/develop",
        )
        payload = _build_normalized_payload(req)
        assert payload["object_attributes"]["title"] == "GL Issue"
        assert payload["user"]["username"] == "bob"
        assert payload["object_attributes"]["target_branch"] == "develop"

    def test_jira_payload(self):
        from app.schemas.trigger import MockWebhookRequest
        from app.services.trigger_service import _build_normalized_payload

        req = MockWebhookRequest(
            source="jira",
            event_type="jira:issue_created",
            title="Jira Task",
            number=100,
            author="carol",
            labels=["sprint-1"],
        )
        payload = _build_normalized_payload(req)
        assert payload["issue"]["key"] == "MOCK-100"
        assert payload["issue"]["fields"]["summary"] == "Jira Task"
        assert payload["issue"]["fields"]["labels"] == ["sprint-1"]

    def test_extra_fields_merged(self):
        from app.schemas.trigger import MockWebhookRequest
        from app.services.trigger_service import _build_normalized_payload

        req = MockWebhookRequest(
            source="webhook",
            event_type="custom",
            title="Custom",
            extra={"custom_key": "custom_value"},
        )
        payload = _build_normalized_payload(req)
        assert payload["custom_key"] == "custom_value"
