from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    jira_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_rmb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    branch_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    pr_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    target_branch: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    yunxiao_task_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    github_issue_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Phase 3.2: Interactive planning
    plan: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Phase 3.3: Dynamic routing decision audit trail
    routing_decisions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Phase 3.4: Template version used for this task
    template_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    template_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("task_templates.id"), nullable=True
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )

    stages: Mapped[List["TaskStageModel"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        # Keep DB query order deterministic; business order is still enforced in service layer.
        order_by="TaskStageModel.id",
    )
    template = relationship("TaskTemplateModel", lazy="selectin")
    project = relationship("ProjectModel", lazy="selectin")


class TaskStageModel(Base):
    __tablename__ = "task_stages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turns_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    self_fix_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Phase 1.1: Structured output extracted from raw text
    output_structured: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Phase 1.2: Failure classification category
    failure_category: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # Phase 2.2: Self-assessment confidence score (0.0 - 1.0)
    self_assessment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Phase 2.5: Per-stage retry count
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Phase 3.1: Execution count for graph loops
    execution_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    task: Mapped[TaskModel] = relationship(back_populates="stages")
