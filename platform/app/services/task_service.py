from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.task import TaskModel, TaskStageModel
from app.models.template import TaskTemplateModel
from app.schemas.task import TaskCreateRequest, TaskDetailResponse, TaskListResponse, TaskStageResponse


class TaskService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_tasks(
        self, page: int = 1, page_size: int = 20, status: Optional[str] = None
    ) -> TaskListResponse:
        query = select(TaskModel).options(selectinload(TaskModel.stages))
        count_query = select(func.count()).select_from(TaskModel)

        if status:
            query = query.where(TaskModel.status == status)
            count_query = count_query.where(TaskModel.status == status)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(TaskModel.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        tasks = result.scalars().all()

        return TaskListResponse(
            items=[self._task_to_response(t) for t in tasks],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create_task(self, request: TaskCreateRequest) -> TaskDetailResponse:
        task = TaskModel(
            title=request.title,
            description=request.description,
            jira_id=request.jira_id,
            status="pending",
            template_id=request.template_id,
            project_id=request.project_id,
        )
        self.session.add(task)
        await self.session.flush()

        if request.template_id:
            template = await self.session.get(TaskTemplateModel, request.template_id)
            if template and template.stages:
                stage_defs = json.loads(template.stages)
                for stage_def in stage_defs:
                    stage = TaskStageModel(
                        task_id=task.id,
                        stage_name=stage_def["name"],
                        agent_role=stage_def["agent_role"],
                        status="pending",
                    )
                    self.session.add(stage)

        await self.session.commit()
        await self.session.refresh(task, attribute_names=["stages"])
        return self._task_to_response(task)

    async def get_task(self, task_id: str) -> Optional[TaskDetailResponse]:
        result = await self.session.execute(
            select(TaskModel)
            .options(selectinload(TaskModel.stages))
            .where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None
        return self._task_to_response(task)

    async def get_stages(self, task_id: str) -> List[TaskStageResponse]:
        result = await self.session.execute(
            select(TaskStageModel)
            .where(TaskStageModel.task_id == task_id)
            .order_by(TaskStageModel.started_at)
        )
        stages = result.scalars().all()
        return [TaskStageResponse.model_validate(s) for s in stages]

    async def cancel_task(self, task_id: str) -> Optional[TaskDetailResponse]:
        result = await self.session.execute(
            select(TaskModel)
            .options(selectinload(TaskModel.stages))
            .where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None
        if task.status in ("completed", "failed", "cancelled"):
            return self._task_to_response(task)
        task.status = "cancelled"
        task.completed_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(task)
        return self._task_to_response(task)

    @staticmethod
    def _task_to_response(task: TaskModel) -> TaskDetailResponse:
        return TaskDetailResponse(
            id=task.id,
            jira_id=task.jira_id,
            title=task.title,
            description=task.description,
            status=task.status,
            total_tokens=task.total_tokens,
            total_cost_rmb=task.total_cost_rmb,
            created_at=task.created_at,
            completed_at=task.completed_at,
            stages=[TaskStageResponse.model_validate(s) for s in task.stages],
            template_id=task.template_id,
            project_id=task.project_id,
            template_name=task.template.display_name if task.template else None,
            project_name=task.project.display_name if task.project else None,
        )
