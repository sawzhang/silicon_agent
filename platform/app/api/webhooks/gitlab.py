import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/gitlab", tags=["webhooks"])


@router.post("")
async def gitlab_webhook(request: Request):
    body = await request.json()
    event_type = body.get("object_kind", "unknown")
    project = body.get("project", {}).get("name", "unknown")
    logger.info("GitLab webhook received: event=%s, project=%s", event_type, project)
    return {"status": "received", "event": event_type, "project": project}
