from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_template_service
from app.schemas.template import (
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdateRequest,
)
from app.services.template_service import TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    service: TemplateService = Depends(get_template_service),
):
    return await service.list_templates()


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    service: TemplateService = Depends(get_template_service),
):
    template = await service.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    request: TemplateCreateRequest,
    service: TemplateService = Depends(get_template_service),
):
    return await service.create_template(request)


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    request: TemplateUpdateRequest,
    service: TemplateService = Depends(get_template_service),
):
    template = await service.update_template(template_id, request)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    service: TemplateService = Depends(get_template_service),
):
    deleted = await service.delete_template(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found or is builtin")
