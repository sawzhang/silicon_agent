from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectIntegrationModel(Base):
    """项目集成配置：每个项目可独立配置各 provider 的 Webhook Secret 和 Access Token。"""

    __tablename__ = "project_integrations"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", name="uq_project_provider"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "github" | "jira" | "gitlab"
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    webhook_secret: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda: secrets.token_hex(32)
    )
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
