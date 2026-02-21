from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
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
        DateTime, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    template_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("task_templates.id"), nullable=True
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )

    stages: Mapped[List["TaskStageModel"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
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
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turns_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    self_fix_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    task: Mapped[TaskModel] = relationship(back_populates="stages")
