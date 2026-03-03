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
import app.models.skill_feedback  # noqa: F401
import app.models.trigger  # noqa: F401
import app.models.integration  # noqa: F401

logger = logging.getLogger(__name__)


def _add_missing_columns(connection) -> None:
    """Add columns defined in models but missing from existing DB tables.

    After adding a column, backfill NULL rows with the column's Python-level
    default so that NOT NULL / schema constraints are satisfied.
    """
    inspector = inspect(connection)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_columns:
                col_type = col.type.compile(connection.dialect)

                # Include SQL DEFAULT when the column has a scalar default,
                # so existing rows get the value immediately (not NULL).
                default_clause = ""
                default_val = None
                if col.server_default is not None:
                    default_clause = f" DEFAULT {col.server_default.arg.text}"
                elif col.default is not None and col.default.is_scalar:
                    default_val = col.default.arg
                    if isinstance(default_val, str):
                        default_clause = f" DEFAULT '{default_val}'"
                    else:
                        default_clause = f" DEFAULT {default_val}"

                stmt = (
                    f"ALTER TABLE {table_name} ADD COLUMN"
                    f" {col.name} {col_type}{default_clause}"
                )
                connection.execute(text(stmt))
                logger.info("Added missing column: %s.%s (%s)", table_name, col.name, col_type)

                # Backfill NULL values with the Python-level default
                default_val = None
                if col.default is not None and col.default.is_scalar:
                    default_val = col.default.arg
                if default_val is not None:
                    update_stmt = (
                        f"UPDATE {table_name} SET {col.name} = :val"
                        f" WHERE {col.name} IS NULL"
                    )
                    connection.execute(text(update_stmt), {"val": default_val})
                    logger.info(
                        "Backfilled %s.%s NULL rows with default=%r",
                        table_name, col.name, default_val,
                    )


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
