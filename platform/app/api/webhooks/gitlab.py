import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.trigger_service import TriggerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/gitlab", tags=["webhooks"])

# GitLab object_kind + action → 标准化 event_type
def _gitlab_event_type(body: dict, header_event: str) -> str:
    kind = body.get("object_kind", "")
    action = (body.get("object_attributes") or {}).get("action", "")
    if kind == "merge_request":
        return f"mr_{action}" if action else "mr_event"
    if kind == "push":
        return "push"
    if kind == "tag_push":
        return "tag_push"
    if kind == "note":
        return "comment_created"
    if kind == "issue":
        return f"issue_{action}" if action else "issue_event"
    return kind or header_event


def _normalize_gitlab_payload(data: dict) -> dict:
    """将 GitLab webhook payload 规范化。"""
    attrs = data.get("object_attributes") or {}
    project = data.get("project") or {}
    user = data.get("user") or {}
    labels_raw = data.get("labels") or []
    labels = [lb.get("title", "") for lb in labels_raw if isinstance(lb, dict)]

    return {
        "mr_iid": attrs.get("iid", ""),
        "mr_title": attrs.get("title", ""),
        "mr_url": attrs.get("url", ""),
        "branch": attrs.get("target_branch", "") or attrs.get("source_branch", ""),
        "project_name": project.get("name", ""),
        "project_path": project.get("path_with_namespace", ""),
        "author": user.get("username", "") or user.get("name", ""),
        "labels": labels,
        "title": attrs.get("title", "") or attrs.get("name", ""),
        "push_branch": data.get("ref", "").replace("refs/heads/", ""),
        "commit_count": data.get("total_commits_count", 0),
        # 保留原始数据供模板使用
        **data,
    }


@router.post("")
async def gitlab_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    if settings.GITLAB_WEBHOOK_SECRET:
        token = request.headers.get("X-Gitlab-Token", "")
        if token != settings.GITLAB_WEBHOOK_SECRET:
            logger.warning("GitLab webhook token verification failed")
            raise HTTPException(status_code=403, detail="Invalid webhook token")

    body = await request.json()
    header_event = request.headers.get("X-Gitlab-Event", "")
    event_type = _gitlab_event_type(body, header_event)
    project_name = (body.get("project") or {}).get("name", "unknown")

    logger.info("GitLab webhook received: event=%s, project=%s", event_type, project_name)

    payload = _normalize_gitlab_payload(body)
    service = TriggerService(session)
    task_id = await service.process_event("gitlab", event_type, payload)

    return {
        "status": "received",
        "event": event_type,
        "project": project_name,
        "task_id": task_id,
    }


@router.post("/{project_id}")
async def gitlab_webhook_project(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """项目级 GitLab webhook：使用项目专属 token 验证，只匹配该项目的触发规则。"""
    from app.services.integration_service import IntegrationService

    integration_svc = IntegrationService(session)
    integration = await integration_svc.get_integration_by_project_provider(project_id, "gitlab")
    if integration is None or not integration.enabled:
        raise HTTPException(status_code=404, detail="GitLab integration not found for this project")

    token = request.headers.get("X-Gitlab-Token", "")
    if token != integration.webhook_secret:
        logger.warning("GitLab project webhook token verification failed project_id=%s", project_id)
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    body = await request.json()
    header_event = request.headers.get("X-Gitlab-Event", "")
    event_type = _gitlab_event_type(body, header_event)
    project_name = (body.get("project") or {}).get("name", "unknown")

    logger.info(
        "GitLab project webhook received: project=%s event=%s gl_project=%s",
        project_id, event_type, project_name,
    )

    payload = _normalize_gitlab_payload(body)
    service = TriggerService(session)
    task_id = await service.process_event("gitlab", event_type, payload, project_id=project_id)

    return {
        "status": "received",
        "event": event_type,
        "project": project_name,
        "project_id": project_id,
        "task_id": task_id,
    }
