from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_trigger_service
from app.schemas.trigger import (
    TriggerEventResponse,
    TriggerRuleCreate,
    TriggerRuleResponse,
    TriggerRuleUpdate,
    TriggerSimulateRequest,
    TriggerSimulateResponse,
    TriggerTestRequest,
    TriggerTestResponse,
)
from app.services.trigger_service import TriggerService

router = APIRouter(prefix="/triggers", tags=["triggers"])


@router.get("", response_model=List[TriggerRuleResponse])
async def list_rules(service: TriggerService = Depends(get_trigger_service)):
    rules = await service.list_rules()
    return [TriggerRuleResponse.model_validate(r) for r in rules]


@router.post("", response_model=TriggerRuleResponse, status_code=201)
async def create_rule(
    request: TriggerRuleCreate,
    service: TriggerService = Depends(get_trigger_service),
):
    data = request.model_dump()
    rule = await service.create_rule(data)
    return TriggerRuleResponse.model_validate(rule)


@router.get("/events", response_model=List[TriggerEventResponse])
async def list_events(
    limit: int = 50,
    service: TriggerService = Depends(get_trigger_service),
):
    events = await service.list_events(limit=limit)
    return [TriggerEventResponse.model_validate(e) for e in events]


@router.post("/simulate", response_model=TriggerSimulateResponse)
async def simulate_trigger_event(
    request: TriggerSimulateRequest,
    service: TriggerService = Depends(get_trigger_service),
):
    """模拟事件触发（dry-run）：返回匹配结果和渲染后的任务信息，不创建任务。"""
    result = await service.simulate_event(request.source, request.event_type, request.payload)
    matched_rule = result.get("matched_rule")
    return TriggerSimulateResponse(
        matched_rule=TriggerRuleResponse.model_validate(matched_rule) if matched_rule else None,
        result=result["result"],
        filter_passed=result["filter_passed"],
        dedup_blocked=result["dedup_blocked"],
        dedup_key=result.get("dedup_key"),
        rendered_title=result.get("rendered_title"),
        rendered_desc=result.get("rendered_desc"),
    )


@router.get("/{rule_id}", response_model=TriggerRuleResponse)
async def get_rule(
    rule_id: str,
    service: TriggerService = Depends(get_trigger_service),
):
    rule = await service.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="触发规则不存在")
    return TriggerRuleResponse.model_validate(rule)


@router.put("/{rule_id}", response_model=TriggerRuleResponse)
async def update_rule(
    rule_id: str,
    request: TriggerRuleUpdate,
    service: TriggerService = Depends(get_trigger_service),
):
    data = {k: v for k, v in request.model_dump().items() if v is not None}
    rule = await service.update_rule(rule_id, data)
    if rule is None:
        raise HTTPException(status_code=404, detail="触发规则不存在")
    return TriggerRuleResponse.model_validate(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: str,
    service: TriggerService = Depends(get_trigger_service),
):
    deleted = await service.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="触发规则不存在")


@router.post("/{rule_id}/test", response_model=TriggerTestResponse)
async def test_trigger_rule(
    rule_id: str,
    request: TriggerTestRequest,
    service: TriggerService = Depends(get_trigger_service),
):
    """对指定规则进行 dry-run 测试：返回过滤/去重结果和渲染后的任务信息，不创建任务。"""
    result = await service.test_rule(rule_id, request.payload)
    if result is None:
        raise HTTPException(status_code=404, detail="触发规则不存在")
    return TriggerTestResponse(**result)
