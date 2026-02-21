from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_skill_service
from app.schemas.skill import SkillCreateRequest, SkillDetailResponse, SkillListResponse, SkillStatsResponse, SkillUpdateRequest
from app.services.skill_service import SkillService

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=SkillListResponse)
async def list_skills(
    page: int = 1,
    page_size: int = 20,
    layer: Optional[str] = None,
    role: Optional[str] = None,
    service: SkillService = Depends(get_skill_service),
):
    return await service.list_skills(page=page, page_size=page_size, layer=layer, role=role)


@router.post("", response_model=SkillDetailResponse, status_code=201)
async def create_skill(
    request: SkillCreateRequest,
    service: SkillService = Depends(get_skill_service),
):
    return await service.create_skill(request)


@router.get("/stats", response_model=SkillStatsResponse)
async def get_skill_stats(service: SkillService = Depends(get_skill_service)):
    return await service.get_stats()


@router.get("/{name}", response_model=SkillDetailResponse)
async def get_skill(name: str, service: SkillService = Depends(get_skill_service)):
    skill = await service.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill


@router.put("/{name}", response_model=SkillDetailResponse)
async def update_skill(
    name: str,
    request: SkillUpdateRequest,
    service: SkillService = Depends(get_skill_service),
):
    skill = await service.update_skill(name, request)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill


@router.delete("/{name}", response_model=SkillDetailResponse)
async def archive_skill(name: str, service: SkillService = Depends(get_skill_service)):
    skill = await service.archive_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill


@router.get("/{name}/versions")
async def get_skill_versions(name: str, service: SkillService = Depends(get_skill_service)):
    versions = await service.get_versions(name)
    return {"name": name, "versions": versions}


@router.post("/{name}/rollback", response_model=SkillDetailResponse)
async def rollback_skill(
    name: str,
    version: str = "1.0.0",
    service: SkillService = Depends(get_skill_service),
):
    skill = await service.rollback(name, version)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill
