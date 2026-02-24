from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_task_log_service
from app.schemas.task_log import TaskLogListResponse
from app.services.task_log_service import TaskLogService

router = APIRouter(prefix="/task-logs", tags=["task-logs"])


@router.get("", response_model=TaskLogListResponse)
async def list_task_logs(
    task: Optional[str] = None,
    task_id: Optional[str] = None,
    stage: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    event_source: Optional[str] = None,
    service: TaskLogService = Depends(get_task_log_service),
):
    task_value = (task or task_id or "").strip()
    if not task_value:
        raise HTTPException(status_code=422, detail="`task` is required")

    return await service.list_logs(
        task_id=task_value,
        stage=stage,
        page=page,
        page_size=page_size,
        event_source=event_source,
    )
