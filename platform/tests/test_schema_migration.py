"""Integration tests for auto-migration (_add_missing_columns).

Uses an isolated in-memory SQLite engine to simulate the full
'old schema → add column → query old data' lifecycle without
affecting other tests.
"""
import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.init_db import _add_missing_columns


@pytest.fixture
def legacy_engine():
    """Create an in-memory SQLite engine with a stripped-down task_stages table
    that is missing columns added later (tokens_used, retry_count, etc.)."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE task_stages ("
            "  id TEXT PRIMARY KEY,"
            "  task_id TEXT NOT NULL,"
            "  stage_name TEXT NOT NULL,"
            "  agent_role TEXT NOT NULL,"
            "  status TEXT NOT NULL DEFAULT 'pending'"
            ")"
        ))
        # Insert a legacy row before the new columns exist
        conn.execute(text(
            "INSERT INTO task_stages (id, task_id, stage_name, agent_role, status)"
            " VALUES ('old-1', 'task-1', 'coding', 'coding', 'completed')"
        ))
    return engine


def test_add_missing_columns_backfills_defaults(legacy_engine):
    """After _add_missing_columns, old rows get default values (not NULL)."""
    with legacy_engine.begin() as conn:
        _add_missing_columns(conn)

    # Verify new columns exist and old row has defaults
    with legacy_engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("task_stages")}
        assert "tokens_used" in cols
        assert "retry_count" in cols
        assert "execution_count" in cols
        assert "turns_used" in cols
        assert "self_fix_count" in cols

        row = conn.execute(
            text("SELECT tokens_used, retry_count, execution_count, turns_used, self_fix_count"
                 " FROM task_stages WHERE id = 'old-1'")
        ).mappings().one()

        assert row["tokens_used"] == 0
        assert row["retry_count"] == 0
        assert row["execution_count"] == 0
        assert row["turns_used"] == 0
        assert row["self_fix_count"] == 0


def test_add_missing_columns_idempotent(legacy_engine):
    """Running _add_missing_columns twice does not error."""
    with legacy_engine.begin() as conn:
        _add_missing_columns(conn)

    # Second run should be a no-op (all columns already exist)
    with legacy_engine.begin() as conn:
        _add_missing_columns(conn)

    with legacy_engine.connect() as conn:
        row = conn.execute(
            text("SELECT tokens_used FROM task_stages WHERE id = 'old-1'")
        ).mappings().one()
        assert row["tokens_used"] == 0
