"""Tests for the project memory store."""
import asyncio
from unittest.mock import patch

import pytest

from app.worker.memory import CATEGORIES, MemoryEntry, ProjectMemoryStore


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Override memory root to a temp directory."""
    with patch("app.worker.memory._MEMORY_ROOT", tmp_path):
        yield tmp_path


def test_memory_entry_create():
    entry = MemoryEntry.create(
        content="Use snake_case for Python files",
        source_task_id="task-1",
        source_task_title="Setup project",
    )
    assert entry.id
    assert entry.content == "Use snake_case for Python files"
    assert entry.confidence == 1.0


@pytest.mark.asyncio
async def test_store_add_and_retrieve(tmp_memory_dir):
    store = ProjectMemoryStore("proj-1")
    entry = MemoryEntry.create(
        content="Always use async/await",
        source_task_id="task-1",
        source_task_title="Init",
    )
    await store.add_entries("conventions", [entry])

    entries = store.get_all_entries("conventions")
    assert len(entries) == 1
    assert entries[0].content == "Always use async/await"


@pytest.mark.asyncio
async def test_store_max_entries(tmp_memory_dir):
    """Entries should be capped at max per category."""
    with patch("app.worker.memory.settings") as mock_settings:
        mock_settings.MEMORY_MAX_ENTRIES_PER_CATEGORY = 3

        store = ProjectMemoryStore("proj-2")
        for i in range(5):
            entry = MemoryEntry.create(
                content=f"Rule {i}",
                source_task_id="task-1",
                source_task_title="Init",
            )
            await store.add_entries("conventions", [entry])

        entries = store.get_all_entries("conventions")
        assert len(entries) == 3
        # Should keep the newest
        assert entries[-1].content == "Rule 4"


@pytest.mark.asyncio
async def test_role_memory_access(tmp_memory_dir):
    store = ProjectMemoryStore("proj-3")

    await store.add_entries("conventions", [
        MemoryEntry.create(content="Use PEP8", source_task_id="t1", source_task_title="Init"),
    ])
    await store.add_entries("issues", [
        MemoryEntry.create(content="SQLite lock issue", source_task_id="t1", source_task_title="Init"),
    ])

    # spec role sees conventions but not issues
    mem = store.get_memory_for_role("spec")
    assert mem is not None
    assert "PEP8" in mem
    assert "SQLite" not in mem

    # test role sees issues but not conventions
    mem = store.get_memory_for_role("test")
    assert mem is not None
    assert "SQLite" in mem
    assert "PEP8" not in mem

    # orchestrator sees everything
    mem = store.get_memory_for_role("orchestrator")
    assert "PEP8" in mem
    assert "SQLite" in mem


def test_empty_memory_returns_none(tmp_memory_dir):
    store = ProjectMemoryStore("proj-empty")
    assert store.get_memory_for_role("coding") is None
