"""External webhook notifier for task lifecycle events.

Sends HTTP POST to NOTIFY_WEBHOOK_URL when configured events occur.
Compatible with Slack Incoming Webhooks, 飞书/钉钉 custom bots, and generic endpoints.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            proxy=None,
        )
    return _client


async def close_notifier() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _is_enabled(event_type: str) -> bool:
    if not settings.NOTIFY_WEBHOOK_URL:
        return False
    allowed = {e.strip() for e in settings.NOTIFY_EVENTS.split(",") if e.strip()}
    return event_type in allowed


async def notify(event_type: str, payload: dict) -> None:
    """Send a notification if the event type is enabled and webhook URL is configured."""
    if not _is_enabled(event_type):
        return

    body = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

    try:
        resp = await _get_client().post(settings.NOTIFY_WEBHOOK_URL, json=body)
        if resp.status_code >= 400:
            logger.warning(
                "Webhook notification failed (status=%d): %s",
                resp.status_code, resp.text[:200],
            )
        else:
            logger.info("Webhook notification sent: %s", event_type)
    except Exception:
        logger.warning("Webhook notification error for %s", event_type, exc_info=True)


async def notify_task_completed(task_id: str, title: str, total_tokens: int) -> None:
    await notify("task_completed", {
        "task_id": task_id,
        "title": title,
        "total_tokens": total_tokens,
        "message": f"任务完成: {title}",
    })


async def notify_task_failed(task_id: str, title: str, reason: str) -> None:
    await notify("task_failed", {
        "task_id": task_id,
        "title": title,
        "reason": reason,
        "message": f"任务失败: {title}\n原因: {reason}",
    })


async def notify_gate_created(
    gate_id: str, task_id: str, stage_name: str, gate_type: str,
) -> None:
    await notify("gate_created", {
        "gate_id": gate_id,
        "task_id": task_id,
        "stage_name": stage_name,
        "gate_type": gate_type,
        "message": f"等待审批: {stage_name} 阶段完成，需要人工审核",
    })
