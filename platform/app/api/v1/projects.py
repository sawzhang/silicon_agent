from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_project_service
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectSyncResponse,
    ProjectUpdateRequest,
)
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    service: ProjectService = Depends(get_project_service),
):
    return await service.list_projects(page=page, page_size=page_size, status=status)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
):
    project = await service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: ProjectCreateRequest,
    service: ProjectService = Depends(get_project_service),
):
    return await service.create_project(request)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    request: ProjectUpdateRequest,
    service: ProjectService = Depends(get_project_service),
):
    project = await service.update_project(project_id, request)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/{project_id}/sync", response_model=ProjectSyncResponse)
async def sync_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
):
    try:
        result = await service.sync_repo(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
):
    deleted = await service.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
