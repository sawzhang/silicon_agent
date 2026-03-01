"""Shared test fixtures for the agent platform."""
import os
import tempfile
from typing import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

# Use a per-process temporary file instead of shared-cache in-memory SQLite.
# File-based SQLite supports WAL mode which eliminates reader-writer blocking —
# the root cause of flakiness when the worker (writer) and test polling (reader)
# run concurrently on the same event loop.
_DB_FILE = os.path.join(tempfile.gettempdir(), f"test_platform_{os.getpid()}.db")
_TEST_DB_URL = f"sqlite+aiosqlite:///{_DB_FILE}"

# Override settings BEFORE any app module creates engines
settings.DATABASE_URL = _TEST_DB_URL
settings.REDIS_URL = ""
settings.JWT_ENABLED = False
settings.WORKER_ENABLED = False
settings.SKILLKIT_ENABLED = False
settings.MEMORY_ENABLED = True
settings.MEMORY_COMPRESSION_ENABLED = False
settings.DEBUG = False  # suppress SQL echo in tests

# Now create a test engine and patch the session module
_test_engine = create_async_engine(
    _TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)


@event.listens_for(_test_engine.sync_engine, "connect")
def _set_sqlite_wal(dbapi_conn, _):
    """Enable WAL journal mode and a generous busy-timeout on every new connection.

    WAL mode: readers never block writers and writers never block readers.
    busy_timeout: on rare write-write conflicts SQLite retries for 30 s instead
    of raising immediately.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


_test_session_factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

# Patch session module to use our test engine
import app.db.session as session_mod  # noqa: E402
session_mod.engine = _test_engine
session_mod.async_session_factory = _test_session_factory

from app.db.init_db import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    """Create all tables once per test session.

    File-based SQLite persists naturally — no keeper connection needed.
    Cleans up the DB file after the session completes.
    """
    await init_db(_test_engine)
    yield
    await _test_engine.dispose()
    for path in [_DB_FILE, _DB_FILE + "-wal", _DB_FILE + "-shm"]:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client pointing at the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
