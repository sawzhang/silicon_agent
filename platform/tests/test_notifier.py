"""Tests for app/integration/notifier.py."""
import logging
from unittest.mock import AsyncMock, MagicMock


import app.integration.notifier as notifier_mod
from app.config import settings
from app.integration.notifier import (
    close_notifier,
    notify,
    notify_gate_created,
    notify_task_completed,
    notify_task_failed,
)


def _mock_response(status_code: int = 200, text: str = "ok") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _reset_client():
    """Ensure notifier _client is None before/after each test."""
    # Close any lingering client
    if notifier_mod._client is not None:
        try:
            await notifier_mod._client.aclose()
        except Exception:
            pass
        notifier_mod._client = None


# ---------------------------------------------------------------------------
# _is_enabled / skip logic
# ---------------------------------------------------------------------------


async def test_notify_skipped_when_no_url(monkeypatch):
    """No NOTIFY_WEBHOOK_URL — notify() returns early without any HTTP call."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_completed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock()
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await notify("task_completed", {"task_id": "t1"})
    fake_client.post.assert_not_awaited()


async def test_notify_skipped_when_event_not_in_list(monkeypatch):
    """Event not in NOTIFY_EVENTS → skip."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_completed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock()
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await notify("task_failed", {"task_id": "t1"})
    fake_client.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# Success / failure cases
# ---------------------------------------------------------------------------


async def test_notify_success(monkeypatch):
    """URL set, event in list, mock returns 200 — post is called once."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_completed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_mock_response(200))
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await notify("task_completed", {"task_id": "t1", "title": "My Task"})

    fake_client.post.assert_awaited_once()
    url, = fake_client.post.call_args[0]
    assert url == "https://example.com/hook"
    body = fake_client.post.call_args[1]["json"]
    assert body["event"] == "task_completed"
    assert body["task_id"] == "t1"
    assert "timestamp" in body


async def test_notify_http_error_logged(monkeypatch, caplog):
    """post returns 500 → warning is logged, no exception raised."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_failed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_mock_response(500, "Server Error"))
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    with caplog.at_level(logging.WARNING, logger="app.integration.notifier"):
        await notify("task_failed", {"task_id": "t2"})

    assert any("500" in r.message or "failed" in r.message.lower() for r in caplog.records)


async def test_notify_exception_swallowed(monkeypatch):
    """If post raises an exception, it is swallowed — no crash."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_completed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=ConnectionError("network down"))
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    # Should not raise
    await notify("task_completed", {"task_id": "t3"})


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


async def test_notify_task_completed(monkeypatch):
    """notify_task_completed sends correct fields."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_completed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_mock_response(200))
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await notify_task_completed("task-1", "Build Feature X", 12345)

    fake_client.post.assert_awaited_once()
    body = fake_client.post.call_args[1]["json"]
    assert body["event"] == "task_completed"
    assert body["task_id"] == "task-1"
    assert body["title"] == "Build Feature X"
    assert body["total_tokens"] == 12345
    assert "任务完成" in body["message"]


async def test_notify_task_failed(monkeypatch):
    """notify_task_failed sends correct fields."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "task_failed")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_mock_response(200))
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await notify_task_failed("task-2", "Fix Bug", "timeout exceeded")

    body = fake_client.post.call_args[1]["json"]
    assert body["event"] == "task_failed"
    assert body["task_id"] == "task-2"
    assert body["reason"] == "timeout exceeded"
    assert "任务失败" in body["message"]


async def test_notify_gate_created(monkeypatch):
    """notify_gate_created sends correct fields."""
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "NOTIFY_EVENTS", "gate_created")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_mock_response(200))
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await notify_gate_created("gate-1", "task-3", "review", "approval")

    body = fake_client.post.call_args[1]["json"]
    assert body["event"] == "gate_created"
    assert body["gate_id"] == "gate-1"
    assert body["task_id"] == "task-3"
    assert body["stage_name"] == "review"
    assert body["gate_type"] == "approval"
    assert "等待审批" in body["message"]


# ---------------------------------------------------------------------------
# close_notifier
# ---------------------------------------------------------------------------


async def test_close_notifier_resets_client(monkeypatch):
    """close_notifier() closes the client and sets _client to None."""
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()
    monkeypatch.setattr(notifier_mod, "_client", fake_client)

    await close_notifier()

    fake_client.aclose.assert_awaited_once()
    assert notifier_mod._client is None
