from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TriggerRuleCreate(BaseModel):
    name: str = Field(..., description="规则名称")
    source: str = Field(..., description="事件来源: jira | gitlab | github | webhook | cron")
    event_type: str = Field(..., description="事件类型，* 表示通配；cron 规则固定为 scheduled")
    filters: Optional[dict[str, Any]] = Field(None, description="过滤条件")
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    title_template: str = Field(default="自动任务: {event_type}", description="任务标题模板")
    desc_template: Optional[str] = Field(None, description="任务描述模板")
    dedup_key_template: Optional[str] = Field(None, description="去重键模板")
    dedup_window_hours: int = Field(default=24, description="去重时间窗口（小时）")
    # Cron 专用字段（仅 source="cron" 时有效）
    cron_expr: Optional[str] = Field(None, description="标准 5 段 cron 表达式，如 '0 9 * * 1-5'")
    enabled: bool = True

    def model_post_init(self, __context: Any) -> None:
        if self.source == "cron":
            if not self.cron_expr:
                raise ValueError("source=cron 时必须提供 cron_expr")
            from app.worker.scheduler import validate_cron_expr
            if not validate_cron_expr(self.cron_expr):
                raise ValueError(f"cron_expr 格式无效: {self.cron_expr}")


class TriggerRuleUpdate(BaseModel):
    name: Optional[str] = None
    source: Optional[str] = None
    event_type: Optional[str] = None
    filters: Optional[dict[str, Any]] = None
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    title_template: Optional[str] = None
    desc_template: Optional[str] = None
    dedup_key_template: Optional[str] = None
    dedup_window_hours: Optional[int] = None
    cron_expr: Optional[str] = None
    enabled: Optional[bool] = None

    def model_post_init(self, __context: Any) -> None:
        if self.cron_expr is not None:
            from app.worker.scheduler import validate_cron_expr
            if not validate_cron_expr(self.cron_expr):
                raise ValueError(f"cron_expr 格式无效: {self.cron_expr}")


class TriggerRuleResponse(BaseModel):
    id: str
    name: str
    source: str
    event_type: str
    filters: Optional[dict[str, Any]]
    template_id: Optional[str]
    project_id: Optional[str]
    title_template: str
    desc_template: Optional[str]
    dedup_key_template: Optional[str]
    dedup_window_hours: int
    cron_expr: Optional[str]
    last_triggered_at: Optional[datetime]
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TriggerEventResponse(BaseModel):
    id: str
    rule_id: Optional[str]
    source: str
    event_type: str
    project_id: Optional[str] = None
    task_id: Optional[str]
    dedup_key: Optional[str]
    result: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TriggerTestRequest(BaseModel):
    payload: dict[str, Any] = Field(..., description="模拟的 webhook payload")


class TriggerTestResponse(BaseModel):
    rule_id: str
    rule_name: str
    filter_passed: bool
    dedup_blocked: bool
    dedup_key: Optional[str]
    rendered_title: str
    rendered_desc: Optional[str]
    would_trigger: bool
    result: str


class TriggerSimulateRequest(BaseModel):
    source: str = Field(..., description="事件来源: github | jira | gitlab | cron")
    event_type: str = Field(..., description="事件类型，如 pr_opened、issue_created")
    payload: dict[str, Any] = Field(..., description="模拟的 webhook payload")


class TriggerSimulateResponse(BaseModel):
    matched_rule: Optional[TriggerRuleResponse]
    result: str  # would_trigger | skipped_no_rule | skipped_filter | skipped_dedup
    filter_passed: bool
    dedup_blocked: bool
    dedup_key: Optional[str]
    rendered_title: Optional[str]
    rendered_desc: Optional[str]
