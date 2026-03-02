"""Tests for complex filter logic (AND/OR/NOT tree) and dry-run endpoints."""
from __future__ import annotations

import pytest
import pytest_asyncio
from app.db.session import async_session_factory
from app.models.trigger import TriggerRuleModel
from app.services.trigger_service import _eval_filter_node, _passes_filters


# ── 复杂过滤器单元测试 ─────────────────────────────────────────────────────────


class TestComplexFilters:
    """_passes_filters 新式布尔表达式树格式测试。"""

    # 基础叶子节点
    def test_leaf_labels_pass(self):
        node = {"type": "labels", "value": ["bug"]}
        assert _eval_filter_node(node, {"labels": ["bug", "feature"]}) is True

    def test_leaf_labels_fail(self):
        node = {"type": "labels", "value": ["urgent"]}
        assert _eval_filter_node(node, {"labels": ["bug"]}) is False

    def test_leaf_labels_empty_payload(self):
        node = {"type": "labels", "value": ["bug"]}
        assert _eval_filter_node(node, {}) is False

    def test_leaf_branch_pass(self):
        node = {"type": "branch", "value": "main"}
        assert _eval_filter_node(node, {"branch": "main"}) is True

    def test_leaf_branch_fail(self):
        node = {"type": "branch", "value": "main"}
        assert _eval_filter_node(node, {"branch": "develop"}) is False

    def test_leaf_title_contains_pass(self):
        node = {"type": "title_contains", "value": "hotfix"}
        assert _eval_filter_node(node, {"title": "Critical hotfix for auth"}) is True

    def test_leaf_title_contains_case_insensitive(self):
        node = {"type": "title_contains", "value": "HOTFIX"}
        assert _eval_filter_node(node, {"title": "apply hotfix now"}) is True

    def test_leaf_title_contains_fail(self):
        node = {"type": "title_contains", "value": "hotfix"}
        assert _eval_filter_node(node, {"title": "regular feature"}) is False

    def test_leaf_author_not_pass(self):
        node = {"type": "author_not", "value": ["bot", "ci"]}
        assert _eval_filter_node(node, {"author": "alice"}) is True

    def test_leaf_author_not_fail(self):
        node = {"type": "author_not", "value": ["bot", "ci"]}
        assert _eval_filter_node(node, {"author": "bot"}) is False

    def test_leaf_unknown_type_passes(self):
        node = {"type": "nonexistent", "value": "x"}
        assert _eval_filter_node(node, {}) is True

    # AND 节点
    def test_and_all_pass(self):
        node = {
            "op": "and",
            "conditions": [
                {"type": "branch", "value": "main"},
                {"type": "labels", "value": ["urgent"]},
            ],
        }
        payload = {"branch": "main", "labels": ["urgent"]}
        assert _eval_filter_node(node, payload) is True

    def test_and_one_fails(self):
        node = {
            "op": "and",
            "conditions": [
                {"type": "branch", "value": "main"},
                {"type": "labels", "value": ["urgent"]},
            ],
        }
        payload = {"branch": "develop", "labels": ["urgent"]}
        assert _eval_filter_node(node, payload) is False

    def test_and_empty_conditions(self):
        node = {"op": "and", "conditions": []}
        assert _eval_filter_node(node, {}) is True  # all() of empty = True

    # OR 节点
    def test_or_one_passes(self):
        node = {
            "op": "or",
            "conditions": [
                {"type": "branch", "value": "main"},
                {"type": "title_contains", "value": "hotfix"},
            ],
        }
        assert _eval_filter_node(node, {"branch": "develop", "title": "critical hotfix"}) is True

    def test_or_all_fail(self):
        node = {
            "op": "or",
            "conditions": [
                {"type": "branch", "value": "main"},
                {"type": "title_contains", "value": "hotfix"},
            ],
        }
        assert _eval_filter_node(node, {"branch": "develop", "title": "regular feature"}) is False

    def test_or_empty_conditions(self):
        node = {"op": "or", "conditions": []}
        assert _eval_filter_node(node, {}) is False  # any() of empty = False

    # NOT 节点
    def test_not_inverts_true(self):
        node = {"op": "not", "conditions": [{"type": "branch", "value": "main"}]}
        assert _eval_filter_node(node, {"branch": "main"}) is False

    def test_not_inverts_false(self):
        node = {"op": "not", "conditions": [{"type": "branch", "value": "main"}]}
        assert _eval_filter_node(node, {"branch": "develop"}) is True

    # 嵌套：(branch=main AND label=urgent) OR title_contains=hotfix
    def test_nested_or_of_and_first_branch(self):
        node = {
            "op": "or",
            "conditions": [
                {
                    "op": "and",
                    "conditions": [
                        {"type": "branch", "value": "main"},
                        {"type": "labels", "value": ["urgent"]},
                    ],
                },
                {"type": "title_contains", "value": "hotfix"},
            ],
        }
        # 走第一个 AND 分支
        assert _eval_filter_node(node, {"branch": "main", "labels": ["urgent"]}) is True

    def test_nested_or_of_and_second_branch(self):
        node = {
            "op": "or",
            "conditions": [
                {
                    "op": "and",
                    "conditions": [
                        {"type": "branch", "value": "main"},
                        {"type": "labels", "value": ["urgent"]},
                    ],
                },
                {"type": "title_contains", "value": "hotfix"},
            ],
        }
        # AND 分支失败，走 title_contains 分支
        assert _eval_filter_node(node, {"branch": "develop", "title": "critical hotfix"}) is True

    def test_nested_or_of_and_both_fail(self):
        node = {
            "op": "or",
            "conditions": [
                {
                    "op": "and",
                    "conditions": [
                        {"type": "branch", "value": "main"},
                        {"type": "labels", "value": ["urgent"]},
                    ],
                },
                {"type": "title_contains", "value": "hotfix"},
            ],
        }
        assert _eval_filter_node(node, {"branch": "develop", "title": "regular feature"}) is False

    # _passes_filters 路由测试
    def test_passes_filters_new_format_dispatches_tree(self):
        filters = {"op": "and", "conditions": [{"type": "branch", "value": "main"}]}
        assert _passes_filters(filters, {"branch": "main"}) is True
        assert _passes_filters(filters, {"branch": "develop"}) is False

    def test_passes_filters_empty(self):
        assert _passes_filters({}, {"branch": "main"}) is True

    def test_passes_filters_unknown_op(self):
        filters = {"op": "xor", "conditions": []}
        assert _passes_filters(filters, {}) is True


class TestBackwardCompatFilters:
    """旧式平铺 AND 格式向后兼容测试。"""

    def test_old_format_labels(self):
        filters = {"labels": ["auto-agent"]}
        assert _passes_filters(filters, {"labels": ["auto-agent", "bug"]}) is True
        assert _passes_filters(filters, {"labels": ["bug"]}) is False

    def test_old_format_branch(self):
        filters = {"branch": "main"}
        assert _passes_filters(filters, {"branch": "main"}) is True
        assert _passes_filters(filters, {"branch": "develop"}) is False

    def test_old_format_title_contains(self):
        filters = {"title_contains": "fix"}
        assert _passes_filters(filters, {"title": "Fix critical bug"}) is True
        assert _passes_filters(filters, {"title": "New feature"}) is False

    def test_old_format_author_not(self):
        filters = {"author_not": ["bot"]}
        assert _passes_filters(filters, {"author": "alice"}) is True
        assert _passes_filters(filters, {"author": "bot"}) is False

    def test_old_format_combined_and(self):
        filters = {"branch": "main", "title_contains": "fix"}
        assert _passes_filters(filters, {"branch": "main", "title": "hotfix"}) is True
        assert _passes_filters(filters, {"branch": "develop", "title": "hotfix"}) is False


# ── Dry-run API 测试 ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_rule():
    """创建一条用于 dry-run 测试的规则，测试后清理。"""
    rule_id = "tt-dryr-1"
    async with async_session_factory() as session:
        rule = TriggerRuleModel(
            id=rule_id,
            name="dry-run test rule",
            source="github",
            event_type="pr_opened",
            filters={
                "op": "and",
                "conditions": [
                    {"type": "branch", "value": "main"},
                    {"type": "labels", "value": ["auto-agent"]},
                ],
            },
            title_template="PR #{pr_number}: {pr_title}",
            desc_template="作者: {author}",
            dedup_key_template="github:pr:{pr_number}",
            dedup_window_hours=24,
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
async def seed_rule_no_filter():
    """无过滤条件的规则。"""
    rule_id = "tt-dryr-2"
    async with async_session_factory() as session:
        rule = TriggerRuleModel(
            id=rule_id,
            name="no-filter rule",
            source="jira",
            event_type="issue_created",
            filters=None,
            title_template="Jira {issue_key}: {issue_title}",
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


class TestTestRuleEndpoint:
    """POST /api/v1/triggers/{rule_id}/test"""

    @pytest.mark.asyncio
    async def test_would_trigger(self, client, seed_rule):
        payload = {
            "branch": "main",
            "labels": ["auto-agent"],
            "pr_number": "42",
            "pr_title": "Add new feature",
            "author": "alice",
        }
        resp = await client.post(f"/api/v1/triggers/{seed_rule}/test", json={"payload": payload})
        assert resp.status_code == 200
        data = resp.json()
        assert data["would_trigger"] is True
        assert data["filter_passed"] is True
        assert data["dedup_blocked"] is False
        assert data["rendered_title"] == "PR #42: Add new feature"
        assert data["rendered_desc"] == "作者: alice"
        assert data["result"] == "would_trigger"

    @pytest.mark.asyncio
    async def test_filter_fails(self, client, seed_rule):
        payload = {"branch": "develop", "labels": ["auto-agent"], "pr_number": "1"}
        resp = await client.post(f"/api/v1/triggers/{seed_rule}/test", json={"payload": payload})
        assert resp.status_code == 200
        data = resp.json()
        assert data["would_trigger"] is False
        assert data["filter_passed"] is False
        assert data["result"] == "skipped_filter"

    @pytest.mark.asyncio
    async def test_label_filter_fails(self, client, seed_rule):
        payload = {"branch": "main", "labels": ["bug"]}
        resp = await client.post(f"/api/v1/triggers/{seed_rule}/test", json={"payload": payload})
        assert resp.status_code == 200
        assert resp.json()["filter_passed"] is False

    @pytest.mark.asyncio
    async def test_rule_not_found(self, client):
        resp = await client.post(
            "/api/v1/triggers/nonexistent-rule/test",
            json={"payload": {"branch": "main"}},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_dedup_key_rendered(self, client, seed_rule):
        payload = {"branch": "main", "labels": ["auto-agent"], "pr_number": "99"}
        resp = await client.post(f"/api/v1/triggers/{seed_rule}/test", json={"payload": payload})
        assert resp.status_code == 200
        assert resp.json()["dedup_key"] == "github:pr:99"


class TestSimulateEndpoint:
    """POST /api/v1/triggers/simulate"""

    @pytest.mark.asyncio
    async def test_would_trigger(self, client, seed_rule):
        body = {
            "source": "github",
            "event_type": "pr_opened",
            "payload": {
                "branch": "main",
                "labels": ["auto-agent"],
                "pr_number": "10",
                "pr_title": "Fix bug",
                "author": "bob",
            },
        }
        resp = await client.post("/api/v1/triggers/simulate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "would_trigger"
        assert data["filter_passed"] is True
        assert data["matched_rule"] is not None
        assert data["matched_rule"]["id"] == seed_rule
        assert data["rendered_title"] == "PR #10: Fix bug"

    @pytest.mark.asyncio
    async def test_skipped_no_rule(self, client):
        body = {
            "source": "unknown_source",
            "event_type": "some_event",
            "payload": {},
        }
        resp = await client.post("/api/v1/triggers/simulate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "skipped_no_rule"
        assert data["matched_rule"] is None

    @pytest.mark.asyncio
    async def test_skipped_filter(self, client, seed_rule):
        body = {
            "source": "github",
            "event_type": "pr_opened",
            "payload": {"branch": "develop", "labels": ["auto-agent"]},
        }
        resp = await client.post("/api/v1/triggers/simulate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "skipped_filter"
        assert data["filter_passed"] is False

    @pytest.mark.asyncio
    async def test_no_filter_rule_passes(self, client, seed_rule_no_filter):
        body = {
            "source": "jira",
            "event_type": "issue_created",
            "payload": {"issue_key": "PROJ-1", "issue_title": "Crash on login"},
        }
        resp = await client.post("/api/v1/triggers/simulate", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "would_trigger"
        assert data["rendered_title"] == "Jira PROJ-1: Crash on login"

    @pytest.mark.asyncio
    async def test_wildcard_event_type(self, client):
        """通配符 * 规则能匹配任意 event_type。"""
        rule_id = "tt-dryr-wc"
        async with async_session_factory() as session:
            rule = TriggerRuleModel(
                id=rule_id,
                name="wildcard rule",
                source="github",
                event_type="*",
                filters=None,
                title_template="GH: {event_type}",
                enabled=True,
            )
            session.add(rule)
            await session.commit()
        try:
            body = {"source": "github", "event_type": "push", "payload": {"event_type": "push"}}
            resp = await client.post("/api/v1/triggers/simulate", json=body)
            assert resp.status_code == 200
            assert resp.json()["result"] == "would_trigger"
            assert resp.json()["rendered_title"] == "GH: push"
        finally:
            async with async_session_factory() as session:
                r = await session.get(TriggerRuleModel, rule_id)
                if r:
                    await session.delete(r)
                    await session.commit()
