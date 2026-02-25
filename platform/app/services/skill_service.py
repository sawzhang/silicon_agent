from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import SkillModel, SkillVersionModel
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
        name: Optional[str] = None,
        layer: Optional[str] = None,
        tag: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
    ) -> SkillListResponse:
        query = select(SkillModel)
        count_query = select(func.count()).select_from(SkillModel)

        if status:
            if status != "all":
                query = query.where(SkillModel.status == status)
                count_query = count_query.where(SkillModel.status == status)

        if name and name.strip():
            pattern = f"%{name.strip()}%"
            name_filter = (SkillModel.name.ilike(pattern) | SkillModel.display_name.ilike(pattern))
            query = query.where(name_filter)
            count_query = count_query.where(name_filter)

        if layer:
            query = query.where(SkillModel.layer == layer)
            count_query = count_query.where(SkillModel.layer == layer)

        if tag and tag.strip():
            # Tags are stored as JSON array; match token string conservatively.
            pattern = f'%"{tag.strip()}"%'
            tag_filter = cast(SkillModel.tags, String).ilike(pattern)
            query = query.where(tag_filter)
            count_query = count_query.where(tag_filter)

        if role and role.strip():
            pattern = f'%"{role.strip()}"%'
            role_filter = cast(SkillModel.applicable_roles, String).ilike(pattern)
            query = query.where(role_filter)
            count_query = count_query.where(role_filter)

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

        # Snapshot current version before overwriting
        snapshot = SkillVersionModel(
            skill_id=skill.id,
            version=skill.version,
            content=skill.content,
            change_summary=f"Snapshot before update to {request.version or skill.version}",
        )
        self.session.add(snapshot)

        update_data = request.model_dump(exclude_unset=True)
        for fld, value in update_data.items():
            setattr(skill, fld, value)

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
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return []

        # Current version + historical snapshots
        versions = [{"version": skill.version, "created_at": skill.updated_at.isoformat(), "current": True}]
        ver_result = await self.session.execute(
            select(SkillVersionModel)
            .where(SkillVersionModel.skill_id == skill.id)
            .order_by(SkillVersionModel.created_at.desc())
        )
        for v in ver_result.scalars().all():
            versions.append({
                "version": v.version,
                "created_at": v.created_at.isoformat(),
                "current": False,
                "change_summary": v.change_summary,
            })
        return versions

    async def rollback(self, name: str, version: str) -> Optional[SkillDetailResponse]:
        result = await self.session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None

        # Find the requested version snapshot
        ver_result = await self.session.execute(
            select(SkillVersionModel).where(
                SkillVersionModel.skill_id == skill.id,
                SkillVersionModel.version == version,
            ).order_by(SkillVersionModel.created_at.desc()).limit(1)
        )
        target = ver_result.scalar_one_or_none()
        if target is None:
            return None

        # Snapshot current state before rollback
        snapshot = SkillVersionModel(
            skill_id=skill.id,
            version=skill.version,
            content=skill.content,
            change_summary=f"Snapshot before rollback to {version}",
        )
        self.session.add(snapshot)

        # Restore
        skill.version = target.version
        skill.content = target.content

        await self.session.commit()
        await self.session.refresh(skill)
        return SkillDetailResponse.model_validate(skill)

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
