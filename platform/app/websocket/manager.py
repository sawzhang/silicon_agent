import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Map backend event constants to frontend WSMessageType values
_EVENT_TYPE_MAP = {
    "agent:status_changed": "agent_status",
    "agent:session_update": "agent_status",
    "task:created": "task_update",
    "task:status_changed": "task_update",
    "task:stage_update": "task_update",
    "task:stage_log": "stage_log",
    "task:log_stream_update": "task_log_stream",
    "gate:created": "gate_created",
    "gate:approved": "gate_resolved",
    "gate:rejected": "gate_resolved",
    "circuit_breaker:triggered": "activity",
    "circuit_breaker:resolved": "activity",
    "kpi:update": "activity",
}


class ConnectionManager:
    """WebSocket connection manager with optional Redis pub/sub fallback to in-process."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._redis = None
        self._use_redis = False

    async def init_redis(self, redis_url: str) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(redis_url)
            await self._redis.ping()
            self._use_redis = True
            logger.info("WebSocket manager connected to Redis")
        except Exception as e:
            logger.warning("Redis unavailable, falling back to in-process broadcast: %s", e)
            self._redis = None
            self._use_redis = False

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket client connected (total: %d)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WebSocket client disconnected (total: %d)", len(self._connections))

    async def broadcast(self, event: str, data: Any = None) -> None:
        """Broadcast a message to all connected clients.

        Emits messages in the format expected by the frontend:
        {"type": "<mapped_type>", "payload": <data>, "timestamp": "<iso>"}
        """
        msg_type = _EVENT_TYPE_MAP.get(event, "activity")
        message = json.dumps({
            "type": msg_type,
            "payload": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if self._use_redis and self._redis:
            try:
                await self._redis.publish("ws:broadcast", message)
            except Exception as e:
                logger.warning("Redis publish failed, using in-process: %s", e)
                await self._broadcast_local(message)
        else:
            await self._broadcast_local(message)

    async def _broadcast_local(self, message: str) -> None:
        disconnected: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_to(self, websocket: WebSocket, event: str, data: Any = None) -> None:
        msg_type = _EVENT_TYPE_MAP.get(event, event)
        message = json.dumps({
            "type": msg_type,
            "payload": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)


ws_manager = ConnectionManager()
