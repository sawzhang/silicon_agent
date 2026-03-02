from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TriggerRuleModel(Base):
    """事件触发规则：外部事件 → 自动创建 Task。"""

    __tablename__ = "trigger_rules"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 事件来源: "jira" | "gitlab" | "github" | "webhook"
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 事件类型: "issue_created" | "mr_opened" | "push" | "*" (通配)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # 过滤条件 JSON: {"labels": ["auto-agent"], "branch": "main", "title_contains": "fix"}
    filters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # 关联任务模板
    template_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("task_templates.id"), nullable=True
    )
    # 关联项目
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )
    # 任务标题模板，支持 {变量} 占位符，如 "处理 Jira {issue_key}: {issue_title}"
    title_template: Mapped[str] = mapped_column(
        String(500), nullable=False, default="自动任务: {event_type}"
    )
    # 任务描述模板（可选）
    desc_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 去重键模板，如 "jira:{issue_key}"（空则不去重）
    dedup_key_template: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 去重时间窗口（小时），默认 24h
    dedup_window_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="24"
    )
    # Cron 表达式（仅 source="cron" 时使用），标准 5 段格式，如 "0 9 * * 1-5"
    cron_expr: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 上次触发时间（Cron 调度器用于判断是否到期）
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class TriggerEventModel(Base):
    """触发事件日志：记录每次收到外部事件的处理结果。"""

    __tablename__ = "trigger_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # 匹配到的规则 ID（未匹配时为空）
    rule_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # 原始 webhook payload
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # 创建的 Task ID（未触发时为空）
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # 计算出的去重键
    dedup_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 关联项目（项目级 webhook 传入）
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    # 处理结果: "triggered" | "skipped_no_rule" | "skipped_filter" | "skipped_dedup"
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
