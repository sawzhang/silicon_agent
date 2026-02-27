from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import memory_extractor


class _StoreStub:
    instances: list["_StoreStub"] = []

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.added: list[tuple[str, list]] = []
        _StoreStub.instances.append(self)

    async def add_entries(self, category: str, entries: list) -> None:
        self.added.append((category, entries))


@pytest.mark.asyncio
async def test_extract_and_store_memories_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_extractor.settings, "MEMORY_ENABLED", False)
    extract = AsyncMock()
    monkeypatch.setattr(memory_extractor, "_llm_extract", extract)

    await memory_extractor.extract_and_store_memories(
        project_id="p1",
        task_id="t1",
        task_title="title",
        stage_outputs=[{"stage": "code", "output": "ok"}],
    )

    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_and_store_memories_handles_empty_or_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_extractor.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(memory_extractor, "ProjectMemoryStore", _StoreStub)

    # Empty combined text -> returns early
    await memory_extractor.extract_and_store_memories(
        project_id="p1",
        task_id="t1",
        task_title="title",
        stage_outputs=[{"stage": "code", "output": ""}],
    )

    # LLM exception -> swallowed
    monkeypatch.setattr(memory_extractor, "_llm_extract", AsyncMock(side_effect=RuntimeError("boom")))
    await memory_extractor.extract_and_store_memories(
        project_id="p2",
        task_id="t2",
        task_title="title",
        stage_outputs=[{"stage": "code", "output": "something"}],
    )


@pytest.mark.asyncio
async def test_extract_and_store_memories_persists_valid_categories(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_extractor.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(memory_extractor, "ProjectMemoryStore", _StoreStub)
    _StoreStub.instances.clear()

    monkeypatch.setattr(
        memory_extractor,
        "_llm_extract",
        AsyncMock(
            return_value=[
                {"category": "conventions", "content": "use snake_case", "tags": ["style"], "confidence": 0.8},
                {"category": "issues", "content": "retry flaky io", "tags": "not-a-list", "confidence": 0.6},
                {"category": "invalid", "content": "ignore me"},
                {"category": "patterns", "content": "   "},
            ]
        ),
    )

    await memory_extractor.extract_and_store_memories(
        project_id="p3",
        task_id="t3",
        task_title="title",
        stage_outputs=[{"stage": "code", "output": "line1"}, {"stage": "test", "output": "line2"}],
    )

    assert len(_StoreStub.instances) == 1
    store = _StoreStub.instances[0]
    assert {cat for cat, _ in store.added} == {"conventions", "issues"}

    conv_entries = next(entries for cat, entries in store.added if cat == "conventions")
    issue_entries = next(entries for cat, entries in store.added if cat == "issues")

    assert len(conv_entries) == 1
    assert conv_entries[0].content == "use snake_case"
    assert conv_entries[0].tags == ["style"]

    assert len(issue_entries) == 1
    assert issue_entries[0].content == "retry flaky io"
    assert issue_entries[0].tags == []


def test_build_combined_text_respects_budget_and_headers():
    out = memory_extractor._build_combined_text(
        [
            {"stage": "code", "output": "A" * 30},
            {"stage": "test", "output": "B" * 30},
        ],
        max_chars=40,
    )
    assert out.startswith("## code")
    assert "..." in out


@pytest.mark.asyncio
async def test_llm_extract_parses_json_and_handles_parse_error(monkeypatch: pytest.MonkeyPatch):
    class _Client:
        async def chat(self, **kwargs):
            assert "messages" in kwargs
            return SimpleNamespace(content='[{"category":"conventions","content":"x"}]')

    monkeypatch.setattr("app.integration.llm_client.get_llm_client", lambda: _Client())
    data = await memory_extractor._llm_extract("title", "content")
    assert isinstance(data, list)
    assert data[0]["category"] == "conventions"

    class _BadClient:
        async def chat(self, **kwargs):
            return SimpleNamespace(content="not-json")

    monkeypatch.setattr("app.integration.llm_client.get_llm_client", lambda: _BadClient())
    bad = await memory_extractor._llm_extract("title", "content")
    assert bad == []
