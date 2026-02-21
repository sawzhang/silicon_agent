import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/jira", tags=["webhooks"])


@router.post("")
async def jira_webhook(request: Request):
    body = await request.json()
    event_type = body.get("webhookEvent", "unknown")
    issue_key = body.get("issue", {}).get("key", "unknown")
    logger.info("Jira webhook received: event=%s, issue=%s", event_type, issue_key)
    return {"status": "received", "event": event_type, "issue": issue_key}
