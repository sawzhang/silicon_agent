"""Tests for app.integration.event_collector module."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integration.event_collector import EventCollector


@pytest.mark.asyncio
async def test_record_audit():
    session = SimpleNamespace(add=lambda obj: None, commit=AsyncMock())
    collector = EventCollector()
    await collector.record_audit(session, "coding", "file_write", {"file": "main.py"})
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_metric():
    session = SimpleNamespace(add=lambda obj: None, commit=AsyncMock())
    collector = EventCollector()
    await collector.record_metric(session, "tokens_used", "coding", 1500.0, "tokens")
    session.commit.assert_awaited_once()
