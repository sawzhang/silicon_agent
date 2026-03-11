"""Guardrails for timezone-aware datetime model columns."""
from __future__ import annotations

from sqlalchemy import DateTime

from app.db.base import Base


def test_all_model_datetime_columns_are_timezone_aware():
    """Every SQLAlchemy DateTime column must be declared with timezone=True."""
    offenders: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            if isinstance(column.type, DateTime) and not bool(column.type.timezone):
                offenders.append(f"{table_name}.{column.name}")

    assert offenders == [], (
        "Found naive DateTime columns; use DateTime(timezone=True): "
        + ", ".join(offenders)
    )
