"""Shared test fixtures for the agent platform."""
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Override settings BEFORE any app module creates engines
settings.DATABASE_URL = "sqlite+aiosqlite:///:memory:"
settings.REDIS_URL = ""
settings.JWT_ENABLED = False
settings.WORKER_ENABLED = False
settings.SKILLKIT_ENABLED = False
settings.MEMORY_ENABLED = True
settings.MEMORY_COMPRESSION_ENABLED = False
settings.DEBUG = False  # suppress SQL echo in tests

# Now create a test engine and patch the session module
_test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
_test_session_factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

# Patch session module to use our test engine
import app.db.session as session_mod  # noqa: E402
session_mod.engine = _test_engine
session_mod.async_session_factory = _test_session_factory

from app.db.init_db import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    """Create all tables once per test session."""
    await init_db(_test_engine)
    yield
    await _test_engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client pointing at the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
