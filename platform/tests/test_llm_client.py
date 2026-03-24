"""Tests for app.integration.llm_client module."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integration import llm_client as mod


@pytest.mark.asyncio
async def test_list_models_parses_response():
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}, {"id": "gpt-4"}]},
    )
    client = mod.LLMClient(api_key="test", base_url="http://localhost")
    client._client = SimpleNamespace(get=AsyncMock(return_value=fake_response))
    models = await client.list_models()
    assert models == ["gpt-4", "gpt-3.5"]


@pytest.mark.asyncio
async def test_list_models_handles_non_list():
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": "not-a-list"},
    )
    client = mod.LLMClient(api_key="test", base_url="http://localhost")
    client._client = SimpleNamespace(get=AsyncMock(return_value=fake_response))
    models = await client.list_models()
    assert models == []


@pytest.mark.asyncio
async def test_list_models_skips_invalid_items():
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [{"id": "ok"}, "not-dict", {"id": ""}, {"no_id": True}]},
    )
    client = mod.LLMClient(api_key="test", base_url="http://localhost")
    client._client = SimpleNamespace(get=AsyncMock(return_value=fake_response))
    models = await client.list_models()
    assert models == ["ok"]


@pytest.mark.asyncio
async def test_close_client():
    client = mod.LLMClient(api_key="test", base_url="http://localhost")
    client._client = SimpleNamespace(aclose=AsyncMock())
    await client.close()
    client._client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_llm_client_singleton(monkeypatch):
    fake_client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(mod, "_client", fake_client)
    await mod.close_llm_client()
    fake_client.close.assert_awaited_once()
    assert mod._client is None
