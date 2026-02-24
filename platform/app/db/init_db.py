import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base

# Import all models so they register with Base.metadata
import app.models.agent  # noqa: F401
import app.models.task  # noqa: F401
import app.models.skill  # noqa: F401
import app.models.gate  # noqa: F401
import app.models.kpi  # noqa: F401
import app.models.audit  # noqa: F401
import app.models.template  # noqa: F401
import app.models.project  # noqa: F401
import app.models.task_log  # noqa: F401

logger = logging.getLogger(__name__)


def _add_missing_columns(connection) -> None:
    """Add columns defined in models but missing from existing DB tables."""
    inspector = inspect(connection)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_columns:
                col_type = col.type.compile(connection.dialect)
                stmt = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"
                connection.execute(text(stmt))
                logger.info("Added missing column: %s.%s (%s)", table_name, col.name, col_type)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
