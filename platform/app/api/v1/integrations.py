from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from app.dependencies import get_integration_service, get_trigger_service
from app.schemas.integration import (
    IntegrationCreateRequest,
    IntegrationResponse,
    IntegrationUpdateRequest,
)
from app.schemas.trigger import (
    MockWebhookRequest,
    MockWebhookResponse,
    TriggerEventResponse,
    TriggerRuleResponse,
)
from app.services.integration_service import IntegrationService
from app.services.trigger_service import TriggerService

router = APIRouter(prefix="/projects", tags=["integrations"])


@router.get("/{project_id}/integrations", response_model=list[IntegrationResponse])
async def list_integrations(
    project_id: str,
    service: IntegrationService = Depends(get_integration_service),
):
    return await service.list_integrations(project_id)


@router.post(
    "/{project_id}/integrations",
    response_model=IntegrationResponse,
    status_code=201,
)
async def create_integration(
    project_id: str,
    request: IntegrationCreateRequest,
    service: IntegrationService = Depends(get_integration_service),
):
    return await service.create_integration(project_id, request)


@router.get(
    "/{project_id}/integrations/{provider}",
    response_model=IntegrationResponse,
)
async def get_integration(
    project_id: str,
    provider: str,
    service: IntegrationService = Depends(get_integration_service),
):
    return await service.get_integration(project_id, provider)


@router.put(
    "/{project_id}/integrations/{provider}",
    response_model=IntegrationResponse,
)
async def update_integration(
    project_id: str,
    provider: str,
    request: IntegrationUpdateRequest,
    service: IntegrationService = Depends(get_integration_service),
):
    return await service.update_integration(project_id, provider, request)


@router.delete(
    "/{project_id}/integrations/{provider}",
    status_code=204,
)
async def delete_integration(
    project_id: str,
    provider: str,
    service: IntegrationService = Depends(get_integration_service),
):
    await service.delete_integration(project_id, provider)


@router.post(
    "/{project_id}/integrations/{provider}/regenerate-secret",
    response_model=IntegrationResponse,
)
async def regenerate_secret(
    project_id: str,
    provider: str,
    service: IntegrationService = Depends(get_integration_service),
):
    return await service.regenerate_secret(project_id, provider)


# ── 项目级触发规则 & 事件历史 ─────────────────────────────────────────────────


@router.get(
    "/{project_id}/triggers",
    response_model=List[TriggerRuleResponse],
)
async def list_project_triggers(
    project_id: str,
    service: TriggerService = Depends(get_trigger_service),
):
    rules = await service.list_rules_by_project(project_id)
    return [TriggerRuleResponse.model_validate(r) for r in rules]


@router.get(
    "/{project_id}/triggers/events",
    response_model=List[TriggerEventResponse],
)
async def list_project_trigger_events(
    project_id: str,
    limit: int = 50,
    service: TriggerService = Depends(get_trigger_service),
):
    events = await service.list_events_by_project(project_id, limit=limit)
    return [TriggerEventResponse.model_validate(e) for e in events]


# ── 模拟 Webhook 触发 ────────────────────────────────────────────────────────


@router.post(
    "/{project_id}/mock-webhook",
    response_model=MockWebhookResponse,
)
async def mock_webhook(
    project_id: str,
    request: MockWebhookRequest,
    service: TriggerService = Depends(get_trigger_service),
):
    """模拟 webhook 触发，跳过 HMAC 验签，直接调用 process_event()。"""
    return await service.mock_webhook(project_id, request)
