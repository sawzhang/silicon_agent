from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trigger import TriggerEventModel, TriggerRuleModel
from app.schemas.task import TaskCreateRequest
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)


class TriggerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def process_event(
        self,
        source: str,
        event_type: str,
        payload: dict,
        project_id: Optional[str] = None,
    ) -> Optional[str]:
        """处理外部事件，匹配规则并创建任务。

        Args:
            project_id: 项目级 webhook 传入时，只匹配该项目的规则。

        Returns:
            创建的 task_id，或 None（未触发）。
        """
        # 查找匹配的启用规则，按创建时间升序确保「首条命中」顺序确定
        query = select(TriggerRuleModel).where(
            TriggerRuleModel.source == source,
            TriggerRuleModel.enabled.is_(True),
        )
        if project_id is not None:
            query = query.where(TriggerRuleModel.project_id == project_id)
        result = await self.session.execute(query.order_by(TriggerRuleModel.created_at))
        rules = result.scalars().all()

        # 过滤 event_type（支持通配符 "*"）
        matched_rules = [
            r for r in rules
            if r.event_type == "*" or r.event_type == event_type
        ]

        if not matched_rules:
            await self._log_event(
                source, event_type, payload, None, None, "skipped_no_rule",
                project_id=project_id,
            )
            logger.info("触发器：无匹配规则 source=%s event=%s", source, event_type)
            return None

        if len(matched_rules) > 1:
            logger.debug(
                "触发器：%d 条规则匹配 source=%s event=%s，采用首条命中策略",
                len(matched_rules), source, event_type,
            )

        # 首条命中策略：依次评估规则，第一条通过过滤+去重检查的规则触发任务，后续规则不再评估
        for rule in matched_rules:
            # 1. 过滤器检查
            if not _passes_filters(rule.filters or {}, payload):
                await self._log_event(
                    source, event_type, payload, rule.id, None, "skipped_filter",
                    project_id=project_id,
                )
                logger.info("触发器：过滤器未通过 rule=%s", rule.name)
                continue

            # 2. 去重检查
            dedup_key = _render_template(rule.dedup_key_template or "", payload) or None
            if dedup_key and await self._is_duplicate(rule, dedup_key):
                await self._log_event(
                    source, event_type, payload, rule.id, dedup_key, "skipped_dedup",
                    project_id=project_id,
                )
                logger.info("触发器：去重跳过 rule=%s dedup_key=%s", rule.name, dedup_key)
                continue

            # 3. 创建任务（首条命中，立即返回，不继续评估后续规则）
            title = _render_template(rule.title_template, payload)
            description = _render_template(rule.desc_template or "", payload) or None

            task_service = TaskService(self.session)
            task = await task_service.create_task(TaskCreateRequest(
                title=title,
                description=description,
                template_id=rule.template_id,
                project_id=rule.project_id,
            ))

            await self._log_event(
                source, event_type, payload, rule.id, dedup_key, "triggered", task.id,
                project_id=project_id,
            )
            logger.info(
                "触发器：创建任务成功 rule=%s task_id=%s title=%s",
                rule.name, task.id, title,
            )
            return task.id

        return None

    async def _is_duplicate(self, rule: TriggerRuleModel, dedup_key: str) -> bool:
        """检查去重窗口内是否已有相同 dedup_key 的触发记录。"""
        window_hours = rule.dedup_window_hours or 24
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        result = await self.session.execute(
            select(TriggerEventModel).where(
                TriggerEventModel.rule_id == rule.id,
                TriggerEventModel.dedup_key == dedup_key,
                TriggerEventModel.result == "triggered",
                TriggerEventModel.created_at >= cutoff,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _log_event(
        self,
        source: str,
        event_type: str,
        payload: dict,
        rule_id: Optional[str],
        dedup_key: Optional[str],
        result: str,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        event = TriggerEventModel(
            source=source,
            event_type=event_type,
            payload=payload,
            rule_id=rule_id,
            dedup_key=dedup_key,
            result=result,
            task_id=task_id,
            project_id=project_id,
        )
        self.session.add(event)
        await self.session.commit()

    # ── 规则 CRUD ────────────────────────────────────────────────────────────

    async def list_rules(self) -> list[TriggerRuleModel]:
        result = await self.session.execute(
            select(TriggerRuleModel).order_by(TriggerRuleModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_rule(self, rule_id: str) -> Optional[TriggerRuleModel]:
        return await self.session.get(TriggerRuleModel, rule_id)

    async def create_rule(self, data: dict) -> TriggerRuleModel:
        rule = TriggerRuleModel(**data)
        self.session.add(rule)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def update_rule(self, rule_id: str, data: dict) -> Optional[TriggerRuleModel]:
        rule = await self.session.get(TriggerRuleModel, rule_id)
        if rule is None:
            return None
        for key, value in data.items():
            setattr(rule, key, value)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def delete_rule(self, rule_id: str) -> bool:
        rule = await self.session.get(TriggerRuleModel, rule_id)
        if rule is None:
            return False
        await self.session.delete(rule)
        await self.session.commit()
        return True

    async def list_rules_by_project(self, project_id: str) -> list[TriggerRuleModel]:
        result = await self.session.execute(
            select(TriggerRuleModel)
            .where(TriggerRuleModel.project_id == project_id)
            .order_by(TriggerRuleModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_events_by_project(
        self, project_id: str, limit: int = 50
    ) -> list[TriggerEventModel]:
        result = await self.session.execute(
            select(TriggerEventModel)
            .where(TriggerEventModel.project_id == project_id)
            .order_by(TriggerEventModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_events(self, limit: int = 50) -> list[TriggerEventModel]:
        result = await self.session.execute(
            select(TriggerEventModel)
            .order_by(TriggerEventModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def simulate_event(
        self,
        source: str,
        event_type: str,
        payload: dict,
    ) -> dict:
        """模拟事件处理（dry-run），不创建任务，返回匹配详情。

        Returns dict with keys:
            matched_rule, result, filter_passed, dedup_blocked, dedup_key,
            rendered_title, rendered_desc
        """
        result = await self.session.execute(
            select(TriggerRuleModel).where(
                TriggerRuleModel.source == source,
                TriggerRuleModel.enabled.is_(True),
            ).order_by(TriggerRuleModel.created_at)
        )
        rules = result.scalars().all()
        matched_rules = [r for r in rules if r.event_type == "*" or r.event_type == event_type]

        if not matched_rules:
            return {
                "matched_rule": None,
                "result": "skipped_no_rule",
                "filter_passed": False,
                "dedup_blocked": False,
                "dedup_key": None,
                "rendered_title": None,
                "rendered_desc": None,
            }

        last: dict = {}
        for rule in matched_rules:
            filter_passed = _passes_filters(rule.filters or {}, payload)
            dedup_key = _render_template(rule.dedup_key_template or "", payload) or None
            dedup_blocked = (
                await self._is_duplicate(rule, dedup_key) if dedup_key else False
            )

            if not filter_passed:
                last = {
                    "matched_rule": rule,
                    "result": "skipped_filter",
                    "filter_passed": False,
                    "dedup_blocked": False,
                    "dedup_key": dedup_key,
                    "rendered_title": _render_template(rule.title_template, payload),
                    "rendered_desc": _render_template(rule.desc_template or "", payload) or None,
                }
                continue

            if dedup_blocked:
                last = {
                    "matched_rule": rule,
                    "result": "skipped_dedup",
                    "filter_passed": True,
                    "dedup_blocked": True,
                    "dedup_key": dedup_key,
                    "rendered_title": _render_template(rule.title_template, payload),
                    "rendered_desc": _render_template(rule.desc_template or "", payload) or None,
                }
                continue

            # 首条命中
            return {
                "matched_rule": rule,
                "result": "would_trigger",
                "filter_passed": True,
                "dedup_blocked": False,
                "dedup_key": dedup_key,
                "rendered_title": _render_template(rule.title_template, payload),
                "rendered_desc": _render_template(rule.desc_template or "", payload) or None,
            }

        return last

    async def test_rule(self, rule_id: str, payload: dict) -> Optional[dict]:
        """对指定规则进行 dry-run 测试，不创建任务。

        Returns None 如果规则不存在，否则返回匹配详情 dict。
        """
        rule = await self.session.get(TriggerRuleModel, rule_id)
        if rule is None:
            return None

        filter_passed = _passes_filters(rule.filters or {}, payload)
        dedup_key = _render_template(rule.dedup_key_template or "", payload) or None
        dedup_blocked = await self._is_duplicate(rule, dedup_key) if dedup_key else False

        would_trigger = filter_passed and not dedup_blocked
        result_str = (
            "would_trigger" if would_trigger
            else ("skipped_dedup" if filter_passed else "skipped_filter")
        )

        return {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "filter_passed": filter_passed,
            "dedup_blocked": dedup_blocked,
            "dedup_key": dedup_key,
            "rendered_title": _render_template(rule.title_template, payload),
            "rendered_desc": _render_template(rule.desc_template or "", payload) or None,
            "would_trigger": would_trigger,
            "result": result_str,
        }


# ── 工具函数 ──────────────────────────────────────────────────────────────────


class _SafeDict(dict):
    """format_map 安全字典：缺失的 key 原样保留 {key}。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_template(template: str, payload: dict) -> str:
    """将 payload 中的字段渲染到模板字符串。"""
    if not template:
        return ""
    flat = _flatten(payload)
    try:
        return template.format_map(_SafeDict(flat))
    except Exception:
        return template


def _flatten(d: dict, prefix: str = "") -> dict:
    """将嵌套 dict 展平为 dot-notation 键，同时保留顶层键。"""
    result: dict = {}
    for k, v in d.items():
        key = f"{prefix}{k}" if prefix else k
        result[key] = v
        if isinstance(v, dict):
            result.update(_flatten(v, f"{key}."))
    return result


def _eval_leaf(node: dict, payload: dict) -> bool:
    """对单个叶子条件节点求值。"""
    t = node.get("type", "")
    v = node.get("value")
    flat = _flatten(payload)

    if t == "labels":
        payload_labels: list = flat.get("labels") or flat.get("issue.fields.labels") or []
        if isinstance(payload_labels, list):
            payload_label_names = [
                lb if isinstance(lb, str) else lb.get("name", "") for lb in payload_labels
            ]
        else:
            payload_label_names = []
        return any(lb in payload_label_names for lb in (v or []))

    if t == "branch":
        branch = (
            flat.get("branch")
            or flat.get("object_attributes.target_branch")
            or flat.get("ref", "").replace("refs/heads/", "")
        )
        return branch == v

    if t == "title_contains":
        title = (
            flat.get("title")
            or flat.get("issue_title")
            or flat.get("object_attributes.title")
            or flat.get("issue.fields.summary", "")
        )
        return str(v or "").lower() in str(title).lower()

    if t == "author_not":
        author = (
            flat.get("author")
            or flat.get("user.username")
            or flat.get("object_attributes.author_id")
            or flat.get("issue.fields.reporter.name", "")
        )
        return str(author) not in [str(a) for a in (v or [])]

    # 未知类型：放行
    return True


def _eval_filter_node(node: dict, payload: dict) -> bool:
    """递归对布尔表达式树节点求值。

    分支节点（有 op 键）：
        {"op": "and", "conditions": [...]}  — 全部为真
        {"op": "or",  "conditions": [...]}  — 任一为真
        {"op": "not", "conditions": [...]}  — 对所有子节点 AND 后取反

    叶子节点（无 op 键）：
        {"type": "labels"|"branch"|"title_contains"|"author_not", "value": ...}
    """
    op = node.get("op")
    if op is None:
        return _eval_leaf(node, payload)
    conditions: list = node.get("conditions") or []
    if op == "and":
        return all(_eval_filter_node(c, payload) for c in conditions)
    if op == "or":
        return any(_eval_filter_node(c, payload) for c in conditions)
    if op == "not":
        return not all(_eval_filter_node(c, payload) for c in conditions)
    # 未知 op：放行
    return True


def _passes_filters(filters: dict, payload: dict) -> bool:
    """对过滤规则求值，兼容旧式平铺格式和新式布尔表达式树格式。

    旧式（向后兼容）：
        {"labels": [...], "branch": "main", "title_contains": "...", "author_not": [...]}
        所有条件 AND 逻辑。

    新式（布尔表达式树，根节点含 "op" 键）：
        {"op": "or", "conditions": [
            {"op": "and", "conditions": [
                {"type": "branch", "value": "main"},
                {"type": "labels", "value": ["urgent"]}
            ]},
            {"type": "title_contains", "value": "hotfix"}
        ]}
    """
    if not filters:
        return True

    # 新式：根节点含 "op" 键 → 使用表达式树求值
    if "op" in filters:
        return _eval_filter_node(filters, payload)

    # 旧式：平铺 AND 逻辑
    flat = _flatten(payload)

    # labels 过滤：payload 中的 labels 列表需包含规则指定的至少一个标签
    if "labels" in filters:
        required_labels: list = filters["labels"]
        payload_labels: list = flat.get("labels") or flat.get("issue.fields.labels") or []
        if isinstance(payload_labels, list):
            # Jira labels 可能是 string list 或 object list
            payload_label_names = [
                lb if isinstance(lb, str) else lb.get("name", "") for lb in payload_labels
            ]
        else:
            payload_label_names = []
        if not any(lb in payload_label_names for lb in required_labels):
            return False

    # branch 过滤
    if "branch" in filters:
        branch = (
            flat.get("branch")
            or flat.get("object_attributes.target_branch")
            or flat.get("ref", "").replace("refs/heads/", "")
        )
        if branch != filters["branch"]:
            return False

    # title_contains 过滤
    if "title_contains" in filters:
        title = (
            flat.get("title")
            or flat.get("issue_title")
            or flat.get("object_attributes.title")
            or flat.get("issue.fields.summary", "")
        )
        if filters["title_contains"].lower() not in str(title).lower():
            return False

    # author_not 过滤
    if "author_not" in filters:
        author = (
            flat.get("author")
            or flat.get("user.username")
            or flat.get("object_attributes.author_id")
            or flat.get("issue.fields.reporter.name", "")
        )
        if str(author) in [str(a) for a in filters["author_not"]]:
            return False

    return True
