from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.task import TaskModel, TaskStageModel
from app.models.template import TaskTemplateModel
from app.schemas.task import (
    TaskBatchCreateRequest,
    TaskBatchCreateResponse,
    TaskBatchRetryItem,
    TaskBatchRetryResponse,
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

    @staticmethod
    def _build_auto_target_branch(task_id: str) -> str:
        suffix = (task_id or "").rsplit("-", 1)[-1].strip() or task_id
        return f"silicon_agent/{suffix}"

    async def list_tasks(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
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

        project_id_value = project_id.strip() if project_id else None
        if project_id_value:
            query = query.where(TaskModel.project_id == project_id_value)
            count_query = count_query.where(TaskModel.project_id == project_id_value)

        title_value = title.strip() if title else None
        if title_value:
            title_filter = func.lower(TaskModel.title).like(f"%{title_value.lower()}%")
            query = query.where(title_filter)
            count_query = count_query.where(title_filter)

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
            id=str(uuid.uuid4()),
            title=request.title,
            description=request.description,
            jira_id=request.jira_id,
            status="pending",
            template_id=request.template_id,
            project_id=request.project_id,
            target_branch=None,
            yunxiao_task_id=request.yunxiao_task_id,
            github_issue_number=request.github_issue_number,
        )
        self.session.add(task)
        await self.session.flush()
        task.target_branch = self._build_auto_target_branch(task.id)

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

    async def clone_task(self, task_id: str) -> Optional[TaskDetailResponse]:
        """Create a new task by copying only safe creation fields from a source task."""
        source_task = await self._load_task_with_relations_optional(task_id)
        if source_task is None:
            return None

        return await self.create_task(
            TaskCreateRequest(
                jira_id=source_task.jira_id,
                title=source_task.title,
                description=source_task.description,
                template_id=source_task.template_id,
                project_id=source_task.project_id,
                yunxiao_task_id=source_task.yunxiao_task_id,
                github_issue_number=getattr(source_task, "github_issue_number", None),
            )
        )

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

    async def update_github_issue_number(self, task_id: str, number: int) -> None:
        result = await self.session.execute(
            select(TaskModel).where(TaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            task.github_issue_number = number
            await self.session.commit()

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
                target_branch=item.target_branch,
                yunxiao_task_id=item.yunxiao_task_id,
            )
            result = await self.create_task(task_req)
            created_tasks.append(result)

        return TaskBatchCreateResponse(
            created=len(created_tasks),
            tasks=created_tasks,
        )

    async def retry_task(self, task_id: str) -> Optional[TaskDetailResponse]:
        """Retry a failed task by resetting failed stages to pending.

        Args:
            task_id: Task identifier.

        Returns:
            Updated task detail when task exists, otherwise ``None``.
        """
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

        # Reset task status to pending (keep completed_at cleared)
        task.status = "pending"
        task.completed_at = None

        # Reset failed stages and keep retry count constraints.
        for stage in task.stages:
            if stage.status != "failed":
                continue

            stage_max_retries = self._resolve_stage_max_retries(task, stage)
            if stage.retry_count >= stage_max_retries:
                logger.info(
                    "Stage %s reached max retries (%d/%d), keeping failed",
                    stage.stage_name, stage.retry_count, stage_max_retries,
                )
                continue
            self._reset_stage_for_retry(stage, increment_retry=True)

        self._recalculate_task_usage(task)

        await self.session.commit()
        self.session.expire_all()

        refreshed = await self._load_task_with_relations(task_id)
        return self._task_to_response(refreshed)

    async def retry_from_stage(self, task_id: str, stage_id: str) -> Optional[TaskDetailResponse]:
        """Retry a failed task from a specific failed stage.

        Args:
            task_id: Task identifier.
            stage_id: Stage identifier that must be in failed status.

        Returns:
            Updated task detail when the task exists, otherwise ``None``.

        Raises:
            LookupError: Stage does not exist on the task.
            ValueError: Task/stage state is not eligible for retry.
        """
        task = await self._load_task_with_relations_optional(task_id)
        if task is None:
            return None
        if task.status != "failed":
            raise ValueError(f"Task status must be failed, got {task.status}")

        target_stage = next((s for s in task.stages if s.id == stage_id), None)
        if target_stage is None:
            raise LookupError("Stage not found in task")
        if target_stage.status != "failed":
            raise ValueError(f"Stage status must be failed, got {target_stage.status}")

        stage_max_retries = self._resolve_stage_max_retries(task, target_stage)
        if target_stage.retry_count >= stage_max_retries:
            raise ValueError(
                f"Stage retry limit reached ({target_stage.retry_count}/{stage_max_retries})"
            )

        task.status = "pending"
        task.completed_at = None
        self._reset_stage_for_retry(target_stage, increment_retry=True)
        self._recalculate_task_usage(task)

        await self.session.commit()
        self.session.expire_all()
        refreshed = await self._load_task_with_relations(task_id)
        return self._task_to_response(refreshed)

    async def retry_batch(self, task_ids: List[str]) -> TaskBatchRetryResponse:
        """Retry multiple failed tasks and return per-task outcomes.

        Args:
            task_ids: Task identifiers to retry.

        Returns:
            Aggregated retry result with per-task success/failure details.
        """
        items: list[TaskBatchRetryItem] = []
        succeeded = 0

        for task_id in task_ids:
            task = await self._load_task_with_relations_optional(task_id)
            if task is None:
                items.append(TaskBatchRetryItem(task_id=task_id, success=False, reason="Task not found"))
                continue
            if task.status != "failed":
                items.append(
                    TaskBatchRetryItem(
                        task_id=task_id,
                        success=False,
                        reason=f"Task status is {task.status}, not failed",
                    )
                )
                continue

            target_stage, reason = self._select_retryable_failed_stage(task)
            if target_stage is None:
                items.append(
                    TaskBatchRetryItem(
                        task_id=task_id,
                        success=False,
                        reason=reason or "No retryable failed stage",
                    )
                )
                continue

            try:
                retried = await self.retry_from_stage(task_id, target_stage.id)
            except (LookupError, ValueError) as exc:
                items.append(TaskBatchRetryItem(task_id=task_id, success=False, reason=str(exc)))
                continue

            if retried is None:
                items.append(
                    TaskBatchRetryItem(task_id=task_id, success=False, reason="Task not found")
                )
                continue
            succeeded += 1
            items.append(TaskBatchRetryItem(task_id=task_id, success=True, task=retried))

        return TaskBatchRetryResponse(
            total=len(task_ids),
            succeeded=succeeded,
            failed=len(task_ids) - succeeded,
            items=items,
        )

    async def _load_task_with_relations_optional(self, task_id: str) -> Optional[TaskModel]:
        """Load a task with stages/template/project relationships."""
        result = await self.session.execute(
            select(TaskModel)
            .options(
                selectinload(TaskModel.stages),
                selectinload(TaskModel.template),
                selectinload(TaskModel.project),
            )
            .where(TaskModel.id == task_id)
        )
        return result.scalar_one_or_none()

    async def _load_task_with_relations(self, task_id: str) -> TaskModel:
        """Load a task with relations and require it to exist."""
        task = await self._load_task_with_relations_optional(task_id)
        if task is None:
            raise LookupError(f"Task {task_id} not found")
        return task

    def _resolve_stage_max_retries(self, task: TaskModel, stage: TaskStageModel) -> int:
        """Resolve max retry count for a stage from template or global default."""
        stage_max_retries = settings.STAGE_DEFAULT_MAX_RETRIES
        if task.template and task.template.stages:
            try:
                stage_defs = json.loads(task.template.stages) if task.template.stages else []
                for sd in stage_defs:
                    if sd.get("name") == stage.stage_name and sd.get("max_retries") is not None:
                        stage_max_retries = sd["max_retries"]
                        break
            except (ValueError, json.JSONDecodeError):
                pass
        return int(stage_max_retries)

    @staticmethod
    def _build_stage_order_map(task: TaskModel) -> dict[str, int]:
        """Build stage order map from template definitions."""
        template = getattr(task, "template", None)
        stage_defs_raw = getattr(template, "stages", None) if template else None
        if not stage_defs_raw:
            return {}
        try:
            stage_defs = json.loads(stage_defs_raw)
        except (ValueError, json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(stage_defs, list):
            return {}

        order_map: dict[str, int] = {}
        for idx, stage_def in enumerate(stage_defs):
            if not isinstance(stage_def, dict):
                continue
            stage_name = stage_def.get("name")
            if not isinstance(stage_name, str) or not stage_name:
                continue
            order_raw = stage_def.get("order", idx)
            try:
                order_map[stage_name] = int(order_raw)
            except (TypeError, ValueError):
                order_map[stage_name] = idx
        return order_map

    @staticmethod
    def _sorted_task_stages(task: TaskModel) -> list[TaskStageModel]:
        """Return task stages in stable display order."""
        order_map = TaskService._build_stage_order_map(task)
        stages = list(task.stages or [])
        return sorted(
            stages,
            key=lambda stage: (
                order_map.get(stage.stage_name, 999),
                getattr(stage, "id", "") or "",
            ),
        )

    def _select_retryable_failed_stage(
        self, task: TaskModel,
    ) -> tuple[Optional[TaskStageModel], Optional[str]]:
        """Pick the next retryable failed stage for batch retry.

        Selection order follows template stage order; if no template is attached,
        keep current task stage order.
        """
        failed_stages = [stage for stage in task.stages if stage.status == "failed"]
        if not failed_stages:
            return None, "No failed stage found"

        order_map: dict[str, int] = {}
        if task.template and task.template.stages:
            try:
                stage_defs = json.loads(task.template.stages) if task.template.stages else []
                order_map = {sd["name"]: int(sd.get("order", idx)) for idx, sd in enumerate(stage_defs)}
            except (ValueError, json.JSONDecodeError, TypeError):
                order_map = {}

        failed_stages.sort(key=lambda stage: order_map.get(stage.stage_name, 999))
        for stage in failed_stages:
            stage_max_retries = self._resolve_stage_max_retries(task, stage)
            if stage.retry_count < stage_max_retries:
                return stage, None
        return None, "All failed stages reached retry limit"

    def _reset_stage_for_retry(self, stage: TaskStageModel, *, increment_retry: bool) -> None:
        """Reset stage runtime fields so the worker can execute it again."""
        stage.status = "pending"
        stage.error_message = None
        stage.failure_category = None
        stage.started_at = None
        stage.completed_at = None
        stage.duration_seconds = None
        stage.tokens_used = 0
        stage.output_summary = None
        stage.output_structured = None
        if increment_retry:
            stage.retry_count += 1

    def _recalculate_task_usage(self, task: TaskModel) -> None:
        """Recompute task token/cost totals from completed stages only."""
        completed_tokens = sum((stage.tokens_used or 0) for stage in task.stages if stage.status == "completed")
        task.total_tokens = completed_tokens
        task.total_cost_rmb = completed_tokens * settings.CB_TOKEN_PRICE_PER_1K / 1000

    @staticmethod
    def _task_to_response(task: TaskModel) -> TaskDetailResponse:
        sorted_stages = TaskService._sorted_task_stages(task)
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
            branch_name=task.branch_name,
            pr_url=task.pr_url,
            stages=[TaskStageResponse.model_validate(s) for s in sorted_stages],
            template_id=task.template_id,
            project_id=task.project_id,
            template_name=task.template.display_name if task.template else None,
            project_name=task.project.display_name if task.project else None,
            target_branch=task.target_branch,
            yunxiao_task_id=task.yunxiao_task_id,
            github_issue_number=getattr(task, "github_issue_number", None),
        )
