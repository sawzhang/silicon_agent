from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.task import TaskModel, TaskStageModel
from app.models.template import TaskTemplateModel
from app.schemas.task import (
    BatchTaskItem,
    TaskBatchCreateRequest,
    TaskBatchCreateResponse,
    TaskCreateRequest,
    TaskDecomposeRequest,
    TaskDecomposeResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskStageResponse,
)

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_tasks(
        self, page: int = 1, page_size: int = 20, status: Optional[str] = None
    ) -> TaskListResponse:
        query = select(TaskModel).options(
            selectinload(TaskModel.stages),
            selectinload(TaskModel.template),
            selectinload(TaskModel.project),
        )
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

        # Re-fetch with eager loading to avoid lazy load issues in async
        result = await self.session.execute(
            select(TaskModel)
            .options(
                selectinload(TaskModel.stages),
                selectinload(TaskModel.template),
                selectinload(TaskModel.project),
            )
            .where(TaskModel.id == task.id)
        )
        task = result.scalar_one()
        return self._task_to_response(task)

    async def get_task(self, task_id: str) -> Optional[TaskDetailResponse]:
        result = await self.session.execute(
            select(TaskModel)
            .options(
                selectinload(TaskModel.stages),
                selectinload(TaskModel.template),
                selectinload(TaskModel.project),
            )
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
            .options(
                selectinload(TaskModel.stages),
                selectinload(TaskModel.template),
                selectinload(TaskModel.project),
            )
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

    async def decompose_prd(self, request: TaskDecomposeRequest) -> TaskDecomposeResponse:
        """Use LLM to decompose a PRD text into suggested subtasks."""
        from app.integration.llm_client import ChatMessage, get_llm_client
        from app.models.project import ProjectModel

        # Build context
        context_parts: list[str] = []

        if request.project_id:
            project = await self.session.get(ProjectModel, request.project_id)
            if project:
                if project.tech_stack:
                    context_parts.append(f"项目技术栈: {', '.join(project.tech_stack)}")
                if project.repo_tree:
                    context_parts.append(f"项目目录结构:\n{project.repo_tree[:1000]}")

        project_context = "\n".join(context_parts) if context_parts else ""

        system_prompt = (
            "你是需求分析专家，擅长将 PRD（产品需求文档）拆分为可独立执行的开发任务。\n"
            "请严格以 JSON 格式输出，不要包含任何其他文字。\n"
            "输出格式:\n"
            '{"tasks": [{"title": "任务标题", "description": "详细描述...\\n\\n验收标准:\\n1. ...\\n2. ...", "priority": "high|medium|low"}], '
            '"summary": "总结说明"}'
        )

        user_content = f"## PRD 内容\n{request.prd_text}"
        if project_context:
            user_content += f"\n\n## 项目上下文\n{project_context}"

        client = get_llm_client()
        llm_response = await client.chat(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_content),
            ],
            temperature=0.3,
            max_tokens=4000,
        )

        # Parse LLM output
        raw = llm_response.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON for decompose: %s", raw[:200])
            return TaskDecomposeResponse(
                tasks=[],
                summary="LLM 返回格式错误，请重试",
                tokens_used=llm_response.total_tokens,
            )

        from app.schemas.task import DecomposedTask

        tasks = []
        for item in parsed.get("tasks", []):
            tasks.append(DecomposedTask(
                title=item.get("title", ""),
                description=item.get("description", ""),
                priority=item.get("priority", "medium"),
            ))

        return TaskDecomposeResponse(
            tasks=tasks,
            summary=parsed.get("summary", f"从 PRD 中识别出 {len(tasks)} 个子任务"),
            tokens_used=llm_response.total_tokens,
        )

    async def batch_create(self, request: TaskBatchCreateRequest) -> TaskBatchCreateResponse:
        """Create multiple tasks at once."""
        created_tasks: list[TaskDetailResponse] = []

        for item in request.tasks:
            task_req = TaskCreateRequest(
                title=item.title,
                description=item.description,
                template_id=item.template_id,
                project_id=item.project_id,
            )
            result = await self.create_task(task_req)
            created_tasks.append(result)

        return TaskBatchCreateResponse(
            created=len(created_tasks),
            tasks=created_tasks,
        )

    async def retry_task(self, task_id: str) -> Optional[TaskDetailResponse]:
        from sqlalchemy import delete as sa_delete

        result = await self.session.execute(
            select(TaskModel)
            .options(
                selectinload(TaskModel.stages),
                selectinload(TaskModel.template),
                selectinload(TaskModel.project),
            )
            .where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None
        if task.status != "failed":
            return self._task_to_response(task)

        # Reset task status to pending
        task.status = "pending"
        task.completed_at = None
        task.total_tokens = 0
        task.total_cost_rmb = 0.0

        # Delete all existing stages via bulk delete
        await self.session.execute(
            sa_delete(TaskStageModel).where(TaskStageModel.task_id == task_id)
        )

        await self.session.commit()

        # Expire cached state so re-fetch gets clean data
        self.session.expire_all()

        # Re-fetch with eager loading
        result = await self.session.execute(
            select(TaskModel)
            .options(
                selectinload(TaskModel.stages),
                selectinload(TaskModel.template),
                selectinload(TaskModel.project),
            )
            .where(TaskModel.id == task_id)
        )
        task = result.scalar_one()
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
