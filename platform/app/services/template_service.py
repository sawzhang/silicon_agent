from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import TaskTemplateModel
from app.schemas.template import (
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdateRequest,
)

logger = logging.getLogger(__name__)

BUILTIN_TEMPLATES = [
    {
        "name": "full_pipeline",
        "display_name": "全流程",
        "description": "完整的9阶段流水线，包含需求解析、方案设计、编码、测试、审计、文档等全部环节",
        "stages": [
            {"name": "parse", "agent_role": "orchestrator", "order": 0},
            {"name": "spec", "agent_role": "spec", "order": 1},
            {"name": "approve", "agent_role": "orchestrator", "order": 2},
            {"name": "code", "agent_role": "coding", "order": 3},
            {"name": "test", "agent_role": "test", "order": 4},
            {"name": "review", "agent_role": "review", "order": 5},
            {"name": "smoke", "agent_role": "smoke", "order": 6},
            {"name": "doc", "agent_role": "doc", "order": 7},
            {"name": "signoff", "agent_role": "orchestrator", "order": 8},
        ],
        "gates": [
            {"after_stage": "spec", "type": "human_approve"},
            {"after_stage": "code", "type": "human_approve"},
            {"after_stage": "signoff", "type": "human_approve"},
        ],
    },
    {
        "name": "quick_fix",
        "display_name": "快速修复",
        "description": "适用于简单bug修复的快速流水线，跳过方案设计和文档阶段",
        "stages": [
            {"name": "parse", "agent_role": "orchestrator", "order": 0},
            {"name": "code", "agent_role": "coding", "order": 1},
            {"name": "test", "agent_role": "test", "order": 2},
            {"name": "signoff", "agent_role": "orchestrator", "order": 3},
        ],
        "gates": [],
    },
    {
        "name": "doc_only",
        "display_name": "文档更新",
        "description": "仅更新文档的轻量流水线",
        "stages": [
            {"name": "parse", "agent_role": "orchestrator", "order": 0},
            {"name": "doc", "agent_role": "doc", "order": 1},
            {"name": "signoff", "agent_role": "orchestrator", "order": 2},
        ],
        "gates": [],
    },
    {
        "name": "review_only",
        "display_name": "代码审计",
        "description": "仅进行代码审计的流水线",
        "stages": [
            {"name": "parse", "agent_role": "orchestrator", "order": 0},
            {"name": "review", "agent_role": "review", "order": 1},
            {"name": "signoff", "agent_role": "orchestrator", "order": 2},
        ],
        "gates": [
            {"after_stage": "review", "type": "human_approve"},
        ],
    },
    {
        "name": "custom",
        "display_name": "自定义",
        "description": "空模版，用户可手动配置阶段",
        "stages": [],
        "gates": [],
    },
]


class TemplateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_templates(self) -> TemplateListResponse:
        result = await self.session.execute(
            select(TaskTemplateModel).order_by(TaskTemplateModel.created_at)
        )
        templates = result.scalars().all()
        items = []
        for t in templates:
            items.append(self._to_response(t))
        return TemplateListResponse(items=items, total=len(items))

    async def get_template(self, template_id: str) -> Optional[TemplateResponse]:
        template = await self.session.get(TaskTemplateModel, template_id)
        if template is None:
            return None
        return self._to_response(template)

    async def create_template(self, request: TemplateCreateRequest) -> TemplateResponse:
        template = TaskTemplateModel(
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            stages=json.dumps([s.model_dump() for s in request.stages], ensure_ascii=False),
            gates=json.dumps([g.model_dump() for g in request.gates], ensure_ascii=False),
            estimated_hours=request.estimated_hours,
        )
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        return self._to_response(template)

    async def update_template(
        self, template_id: str, request: TemplateUpdateRequest
    ) -> Optional[TemplateResponse]:
        template = await self.session.get(TaskTemplateModel, template_id)
        if template is None:
            return None
        if template.is_builtin:
            return self._to_response(template)
        if request.display_name is not None:
            template.display_name = request.display_name
        if request.description is not None:
            template.description = request.description
        if request.stages is not None:
            template.stages = json.dumps(
                [s.model_dump() for s in request.stages], ensure_ascii=False
            )
        if request.gates is not None:
            template.gates = json.dumps(
                [g.model_dump() for g in request.gates], ensure_ascii=False
            )
        if request.estimated_hours is not None:
            template.estimated_hours = request.estimated_hours
        await self.session.commit()
        await self.session.refresh(template)
        return self._to_response(template)

    async def delete_template(self, template_id: str) -> bool:
        template = await self.session.get(TaskTemplateModel, template_id)
        if template is None:
            return False
        if template.is_builtin:
            return False
        await self.session.delete(template)
        await self.session.commit()
        return True

    async def seed_builtin_templates(self) -> None:
        for tpl_data in BUILTIN_TEMPLATES:
            result = await self.session.execute(
                select(TaskTemplateModel).where(TaskTemplateModel.name == tpl_data["name"])
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                template = TaskTemplateModel(
                    name=tpl_data["name"],
                    display_name=tpl_data["display_name"],
                    description=tpl_data["description"],
                    stages=json.dumps(tpl_data["stages"], ensure_ascii=False),
                    gates=json.dumps(tpl_data["gates"], ensure_ascii=False),
                    is_builtin=True,
                )
                self.session.add(template)
                logger.info("Seeded builtin template: %s", tpl_data["name"])
        await self.session.commit()

    @staticmethod
    def _to_response(template: TaskTemplateModel) -> TemplateResponse:
        return TemplateResponse(
            id=template.id,
            name=template.name,
            display_name=template.display_name,
            description=template.description,
            stages=json.loads(template.stages) if template.stages else [],
            gates=json.loads(template.gates) if template.gates else [],
            estimated_hours=template.estimated_hours,
            is_builtin=template.is_builtin,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )
