import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/jira", tags=["webhooks"])


def _verify_jira_signature(body: bytes, signature: str | None) -> bool:
    """Verify Jira webhook HMAC-SHA256 signature if secret is configured."""
    if not settings.JIRA_WEBHOOK_SECRET:
        return True  # no secret configured, skip verification
    if not signature:
        return False
    expected = hmac.new(
        settings.JIRA_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("")
async def jira_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature")

    if not _verify_jira_signature(body, signature):
        logger.warning("Jira webhook signature verification failed")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    import json
    data = json.loads(body)
    event_type = data.get("webhookEvent", "unknown")
    issue_key = data.get("issue", {}).get("key", "unknown")
    logger.info("Jira webhook received: event=%s, issue=%s", event_type, issue_key)
    return {"status": "received", "event": event_type, "issue": issue_key}
