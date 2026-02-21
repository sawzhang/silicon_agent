from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import ProjectModel
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)


class ProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_projects(
        self, page: int = 1, page_size: int = 20, status: Optional[str] = None
    ) -> ProjectListResponse:
        query = select(ProjectModel)
        count_query = select(func.count()).select_from(ProjectModel)

        if status:
            query = query.where(ProjectModel.status == status)
            count_query = count_query.where(ProjectModel.status == status)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(ProjectModel.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        projects = result.scalars().all()

        return ProjectListResponse(
            items=[ProjectResponse.model_validate(p) for p in projects],
            total=total,
        )

    async def get_project(self, project_id: str) -> Optional[ProjectResponse]:
        project = await self.session.get(ProjectModel, project_id)
        if project is None:
            return None
        return ProjectResponse.model_validate(project)

    async def create_project(self, request: ProjectCreateRequest) -> ProjectResponse:
        project = ProjectModel(
            name=request.name,
            display_name=request.display_name,
            repo_url=request.repo_url,
            branch=request.branch,
            description=request.description,
        )
        self.session.add(project)
        await self.session.commit()
        await self.session.refresh(project)
        return ProjectResponse.model_validate(project)

    async def update_project(
        self, project_id: str, request: ProjectUpdateRequest
    ) -> Optional[ProjectResponse]:
        project = await self.session.get(ProjectModel, project_id)
        if project is None:
            return None
        if request.display_name is not None:
            project.display_name = request.display_name
        if request.repo_url is not None:
            project.repo_url = request.repo_url
        if request.branch is not None:
            project.branch = request.branch
        if request.description is not None:
            project.description = request.description
        if request.status is not None:
            project.status = request.status
        await self.session.commit()
        await self.session.refresh(project)
        return ProjectResponse.model_validate(project)

    async def delete_project(self, project_id: str) -> bool:
        project = await self.session.get(ProjectModel, project_id)
        if project is None:
            return False
        await self.session.delete(project)
        await self.session.commit()
        return True
