from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import SkillModel
from app.schemas.skill import (
    SkillCreateRequest,
    SkillDetailResponse,
    SkillListResponse,
    SkillStatsResponse,
    SkillUpdateRequest,
)


class SkillService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_skills(
        self,
        page: int = 1,
        page_size: int = 20,
        layer: Optional[str] = None,
        role: Optional[str] = None,
    ) -> SkillListResponse:
        query = select(SkillModel).where(SkillModel.status != "archived")
        count_query = select(func.count()).select_from(SkillModel).where(SkillModel.status != "archived")

        if layer:
            query = query.where(SkillModel.layer == layer)
            count_query = count_query.where(SkillModel.layer == layer)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(SkillModel.name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        skills = result.scalars().all()

        return SkillListResponse(
            items=[SkillDetailResponse.model_validate(s) for s in skills],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create_skill(self, request: SkillCreateRequest) -> SkillDetailResponse:
        skill = SkillModel(
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            layer=request.layer,
            tags=request.tags,
            applicable_roles=request.applicable_roles,
            content=request.content,
            git_path=request.git_path,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return SkillDetailResponse.model_validate(skill)

    async def get_skill(self, name: str) -> Optional[SkillDetailResponse]:
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None
        return SkillDetailResponse.model_validate(skill)

    async def update_skill(self, name: str, request: SkillUpdateRequest) -> Optional[SkillDetailResponse]:
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None

        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(skill, field, value)

        await self.session.commit()
        await self.session.refresh(skill)
        return SkillDetailResponse.model_validate(skill)

    async def archive_skill(self, name: str) -> Optional[SkillDetailResponse]:
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None
        skill.status = "archived"
        await self.session.commit()
        await self.session.refresh(skill)
        return SkillDetailResponse.model_validate(skill)

    async def get_versions(self, name: str) -> List[dict]:
        skill = await self.get_skill(name)
        if skill is None:
            return []
        return [{"version": skill.version, "created_at": skill.created_at.isoformat()}]

    async def rollback(self, name: str, version: str) -> Optional[SkillDetailResponse]:
        return await self.get_skill(name)

    async def get_stats(self) -> SkillStatsResponse:
        total_result = await self.session.execute(
            select(func.count()).select_from(SkillModel).where(SkillModel.status != "archived")
        )
        total = total_result.scalar() or 0

        by_layer: Dict[str, int] = {}
        for layer_val in ("L1", "L2", "L3"):
            count_result = await self.session.execute(
                select(func.count())
                .select_from(SkillModel)
                .where(SkillModel.layer == layer_val, SkillModel.status != "archived")
            )
            count = count_result.scalar() or 0
            if count > 0:
                by_layer[layer_val] = count

        by_status: Dict[str, int] = {}
        for status_val in ("active", "draft", "archived"):
            count_result = await self.session.execute(
                select(func.count())
                .select_from(SkillModel)
                .where(SkillModel.status == status_val)
            )
            count = count_result.scalar() or 0
            if count > 0:
                by_status[status_val] = count

        return SkillStatsResponse(total=total, by_layer=by_layer, by_status=by_status)
