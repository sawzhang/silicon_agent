"""Tests for app/websocket/manager.py ConnectionManager."""
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock


from app.websocket.manager import ConnectionManager


def _make_ws():
    """Return a lightweight fake WebSocket."""
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


async def test_connect_accepts_and_stores():
    """connect() calls ws.accept() and adds ws to _connections."""
    mgr = ConnectionManager()
    ws = _make_ws()
    await mgr.connect(ws)
    ws.accept.assert_awaited_once()
    assert ws in mgr._connections


async def test_disconnect_removes_ws():
    """disconnect() removes the ws from _connections."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr._connections.append(ws)
    mgr.disconnect(ws)
    assert ws not in mgr._connections


async def test_disconnect_absent_is_noop():
    """disconnect() with an unknown ws does not raise."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr.disconnect(ws)  # should not raise


# ---------------------------------------------------------------------------
# _broadcast_local
# ---------------------------------------------------------------------------


async def test_broadcast_local_sends_to_all():
    """_broadcast_local sends the message string to every connected ws."""
    mgr = ConnectionManager()
    ws1 = _make_ws()
    ws2 = _make_ws()
    mgr._connections = [ws1, ws2]
    await mgr._broadcast_local("hello")
    ws1.send_text.assert_awaited_once_with("hello")
    ws2.send_text.assert_awaited_once_with("hello")


async def test_broadcast_local_cleans_up_failed_ws():
    """If send_text raises, that ws is removed from _connections."""
    mgr = ConnectionManager()
    good_ws = _make_ws()
    bad_ws = _make_ws()
    bad_ws.send_text = AsyncMock(side_effect=RuntimeError("connection closed"))
    mgr._connections = [good_ws, bad_ws]
    await mgr._broadcast_local("msg")
    assert good_ws in mgr._connections
    assert bad_ws not in mgr._connections
    good_ws.send_text.assert_awaited_once_with("msg")


# ---------------------------------------------------------------------------
# broadcast — event type mapping & Redis paths
# ---------------------------------------------------------------------------


async def test_broadcast_maps_event_type():
    """'task:status_changed' is mapped to type='task_update' in the JSON payload."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr._connections = [ws]
    await mgr.broadcast("task:status_changed", {"id": "1"})
    ws.send_text.assert_awaited_once()
    payload = json.loads(ws.send_text.call_args[0][0])
    assert payload["type"] == "task_update"
    assert payload["payload"] == {"id": "1"}
    assert "timestamp" in payload


async def test_broadcast_unknown_event_uses_activity():
    """Unknown events fall back to type='activity'."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr._connections = [ws]
    await mgr.broadcast("some:unknown:event", {"x": 1})
    payload = json.loads(ws.send_text.call_args[0][0])
    assert payload["type"] == "activity"


async def test_broadcast_redis_path():
    """When _use_redis=True, broadcast publishes to Redis instead of calling local send."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr._connections = [ws]
    fake_redis = MagicMock()
    fake_redis.publish = AsyncMock()
    mgr._redis = fake_redis
    mgr._use_redis = True

    await mgr.broadcast("task:created", {"task_id": "abc"})

    fake_redis.publish.assert_awaited_once()
    channel, message = fake_redis.publish.call_args[0]
    assert channel == "ws:broadcast"
    parsed = json.loads(message)
    assert parsed["type"] == "task_update"
    # The ws was NOT sent to directly (Redis path used instead)
    ws.send_text.assert_not_awaited()


async def test_broadcast_redis_fallback():
    """If redis.publish raises, _broadcast_local is called as fallback."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr._connections = [ws]
    fake_redis = MagicMock()
    fake_redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
    mgr._redis = fake_redis
    mgr._use_redis = True

    await mgr.broadcast("gate:created", {"gate_id": "g1"})

    # Fell back to local broadcast — ws received the message
    ws.send_text.assert_awaited_once()
    parsed = json.loads(ws.send_text.call_args[0][0])
    assert parsed["type"] == "gate_created"


# ---------------------------------------------------------------------------
# init_redis
# ---------------------------------------------------------------------------


async def test_init_redis_success(monkeypatch):
    """Successful Redis ping sets _use_redis=True."""
    fake_client = MagicMock()
    fake_client.ping = AsyncMock()
    fake_module = types.ModuleType("redis.asyncio")
    fake_module.from_url = MagicMock(return_value=fake_client)

    # Ensure sub-module is registered
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_module)

    mgr = ConnectionManager()
    await mgr.init_redis("redis://localhost:6379")
    assert mgr._use_redis is True
    assert mgr._redis is fake_client


async def test_init_redis_failure(monkeypatch):
    """If ping raises, _use_redis stays False."""
    fake_client = MagicMock()
    fake_client.ping = AsyncMock(side_effect=ConnectionError("no redis"))
    fake_module = types.ModuleType("redis.asyncio")
    fake_module.from_url = MagicMock(return_value=fake_client)

    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_module)

    mgr = ConnectionManager()
    await mgr.init_redis("redis://localhost:6379")
    assert mgr._use_redis is False
    assert mgr._redis is None


# ---------------------------------------------------------------------------
# send_to
# ---------------------------------------------------------------------------


async def test_send_to_maps_event():
    """send_to maps 'agent:status_changed' → type='agent_status'."""
    mgr = ConnectionManager()
    ws = _make_ws()
    mgr._connections = [ws]
    await mgr.send_to(ws, "agent:status_changed", {"role": "coding"})
    ws.send_text.assert_awaited_once()
    parsed = json.loads(ws.send_text.call_args[0][0])
    assert parsed["type"] == "agent_status"
    assert parsed["payload"] == {"role": "coding"}


async def test_send_to_disconnect_on_error():
    """If send_text raises in send_to, the ws is removed from _connections."""
    mgr = ConnectionManager()
    ws = _make_ws()
    ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))
    mgr._connections = [ws]
    await mgr.send_to(ws, "task:stage_update", {})
    assert ws not in mgr._connections
