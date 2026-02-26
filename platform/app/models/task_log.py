from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskStageLogModel(Base):
    __tablename__ = "task_stage_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("task_stages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    agent_role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success", index=True)

    request_body: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    response_body: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    command: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    command_args: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    workspace: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    execution_mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    missing_fields: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )
