from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_task_service
from app.schemas.task import (
    TaskBatchCreateRequest,
    TaskBatchCreateResponse,
    TaskCreateRequest,
    TaskDecomposeRequest,
    TaskDecomposeResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskStageResponse,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    service: TaskService = Depends(get_task_service),
):
    return await service.list_tasks(page=page, page_size=page_size, status=status)


@router.post("", response_model=TaskDetailResponse, status_code=201)
async def create_task(
    request: TaskCreateRequest,
    service: TaskService = Depends(get_task_service),
):
    return await service.create_task(request)


@router.post("/decompose", response_model=TaskDecomposeResponse)
async def decompose_prd(
    request: TaskDecomposeRequest,
    service: TaskService = Depends(get_task_service),
):
    return await service.decompose_prd(request)


@router.post("/batch", response_model=TaskBatchCreateResponse, status_code=201)
async def batch_create_tasks(
    request: TaskBatchCreateRequest,
    service: TaskService = Depends(get_task_service),
):
    return await service.batch_create(request)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str, service: TaskService = Depends(get_task_service)):
    task = await service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/stages", response_model=List[TaskStageResponse])
async def get_task_stages(task_id: str, service: TaskService = Depends(get_task_service)):
    return await service.get_stages(task_id)


@router.post("/{task_id}/cancel", response_model=TaskDetailResponse)
async def cancel_task(task_id: str, service: TaskService = Depends(get_task_service)):
    task = await service.cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{task_id}/retry", response_model=TaskDetailResponse)
async def retry_task(task_id: str, service: TaskService = Depends(get_task_service)):
    task = await service.retry_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
