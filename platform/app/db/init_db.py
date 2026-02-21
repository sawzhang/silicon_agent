from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base

# Import all models so they register with Base.metadata
import app.models.agent  # noqa: F401
import app.models.task  # noqa: F401
import app.models.skill  # noqa: F401
import app.models.gate  # noqa: F401
import app.models.kpi  # noqa: F401
import app.models.audit  # noqa: F401


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
