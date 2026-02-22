import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/gitlab", tags=["webhooks"])


@router.post("")
async def gitlab_webhook(request: Request):
    # GitLab uses a simple token header for verification
    if settings.GITLAB_WEBHOOK_SECRET:
        token = request.headers.get("X-Gitlab-Token", "")
        if token != settings.GITLAB_WEBHOOK_SECRET:
            logger.warning("GitLab webhook token verification failed")
            raise HTTPException(status_code=403, detail="Invalid webhook token")

    body = await request.json()
    event_type = body.get("object_kind", "unknown")
    project = body.get("project", {}).get("name", "unknown")
    logger.info("GitLab webhook received: event=%s, project=%s", event_type, project)
    return {"status": "received", "event": event_type, "project": project}
