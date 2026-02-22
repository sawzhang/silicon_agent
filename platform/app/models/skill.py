from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SkillModel(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    layer: Mapped[str] = mapped_column(String(10), nullable=False, default="L1")
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    applicable_roles: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    git_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list["SkillVersionModel"]] = relationship(
        back_populates="skill", order_by="SkillVersionModel.created_at.desc()"
    )


class SkillVersionModel(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    skill: Mapped["SkillModel"] = relationship(back_populates="versions")
