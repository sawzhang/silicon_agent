from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.gate import HumanGateModel
from app.models.task import TaskModel
from app.schemas.gate import GateApproveRequest, GateDetailResponse, GateListResponse, GateRejectRequest, GateReviseRequest

logger = logging.getLogger(__name__)


class GateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_gates(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> GateListResponse:
        query = select(HumanGateModel)
        count_query = select(func.count()).select_from(HumanGateModel)

        if status:
            query = query.where(HumanGateModel.status == status)
            count_query = count_query.where(HumanGateModel.status == status)
        if task_id:
            query = query.where(HumanGateModel.task_id == task_id)
            count_query = count_query.where(HumanGateModel.task_id == task_id)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(HumanGateModel.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        gates = result.scalars().all()

        return GateListResponse(
            items=[GateDetailResponse.model_validate(g) for g in gates],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_gate(self, gate_id: str) -> Optional[GateDetailResponse]:
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        return GateDetailResponse.model_validate(gate)

    async def approve(self, gate_id: str, request: GateApproveRequest) -> Optional[GateDetailResponse]:
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        gate.status = "approved"
        gate.reviewer = request.reviewer
        gate.review_comment = request.comment
        gate.reviewed_at = datetime.now()
        await self.session.commit()
        await self.session.refresh(gate)
        return GateDetailResponse.model_validate(gate)

    async def reject(self, gate_id: str, request: GateRejectRequest) -> Optional[GateDetailResponse]:
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        gate.status = "rejected"
        gate.reviewer = request.reviewer
        gate.review_comment = request.comment
        gate.reviewed_at = datetime.now()
        await self.session.commit()
        await self.session.refresh(gate)

        # Extract structured lesson from gate rejection and persist to memory
        if settings.MEMORY_ENABLED and settings.SKILL_FEEDBACK_ENABLED and gate.task_id:
            try:
                await _extract_gate_feedback(self.session, gate)
            except Exception:
                logger.warning(
                    "Gate feedback extraction failed for gate %s", gate_id, exc_info=True
                )

        return GateDetailResponse.model_validate(gate)

    async def revise(self, gate_id: str, request: GateReviseRequest) -> Optional[GateDetailResponse]:
        """Phase 2.4: Revise and continue — approve with modifications."""
        result = await self.session.execute(
            select(HumanGateModel).where(HumanGateModel.id == gate_id)
        )
        gate = result.scalar_one_or_none()
        if gate is None:
            return None
        gate.status = "revised"
        gate.reviewer = request.reviewer
        gate.review_comment = request.comment
        gate.revised_content = request.revised_content
        gate.reviewed_at = datetime.now()
        await self.session.commit()
        await self.session.refresh(gate)
        return GateDetailResponse.model_validate(gate)

    async def get_history(
        self, page: int = 1, page_size: int = 20
    ) -> GateListResponse:
        query = (
            select(HumanGateModel)
            .where(HumanGateModel.status.in_(["approved", "rejected"]))
            .order_by(HumanGateModel.reviewed_at.desc())
        )
        count_query = (
            select(func.count())
            .select_from(HumanGateModel)
            .where(HumanGateModel.status.in_(["approved", "rejected"]))
        )

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(query)
        gates = result.scalars().all()

        return GateListResponse(
            items=[GateDetailResponse.model_validate(g) for g in gates],
            total=total,
            page=page,
            page_size=page_size,
        )


async def _extract_gate_feedback(session: AsyncSession, gate: HumanGateModel) -> None:
    """Extract structured lesson from gate rejection and persist to memory + feedback model."""
    comment = gate.review_comment or ""
    summary = ""
    if gate.content and isinstance(gate.content, dict):
        summary = gate.content.get("summary", "")

    if not comment and not summary:
        return

    # Look up task to get project_id
    task_result = await session.execute(
        select(TaskModel).where(TaskModel.id == gate.task_id)
    )
    task = task_result.scalar_one_or_none()
    if not task:
        return

    # Use LLM to extract structured lesson
    lesson_content = await _llm_extract_gate_lesson(comment, summary)

    # Write to SkillFeedbackModel
    from app.models.skill_feedback import SkillFeedbackModel
    feedback = SkillFeedbackModel(
        skill_name=f"gate:{gate.gate_type}",
        task_id=gate.task_id,
        stage_name=gate.agent_role,
        agent_role=gate.agent_role,
        feedback_type="gate_reject",
        content=lesson_content,
        tokens_used=0,
        duration_ms=0.0,
    )
    session.add(feedback)
    await session.commit()

    # Persist to project memory if project_id is available
    if task.project_id:
        from app.worker.memory import MemoryEntry, ProjectMemoryStore
        store = ProjectMemoryStore(str(task.project_id))
        entry = MemoryEntry.create(
            content=lesson_content,
            source_task_id=str(task.id),
            source_task_title=task.title,
            confidence=1.0,  # Human feedback = highest confidence
            tags=["gate-reject", gate.agent_role],
        )
        await store.add_entries("issues", [entry])

    logger.info("Gate feedback extracted for gate %s, task %s", gate.id, gate.task_id)


async def _llm_extract_gate_lesson(comment: str, summary: str) -> str:
    """Call LLM to extract a structured lesson from gate rejection feedback.

    Falls back to raw comment if LLM is unavailable.
    """
    try:
        from app.integration.llm_client import ChatMessage, get_llm_client
        client = get_llm_client()
        prompt = (
            "你是一个知识提取助手。以下是一个 gate 审批被拒绝的信息：\n\n"
            f"**审批者反馈:** {comment}\n"
            f"**阶段摘要:** {summary}\n\n"
            "请提取一条简洁的经验教训（一句话），可以帮助未来类似任务避免同样的问题。\n"
            "直接输出教训内容，不要添加任何前缀或格式标记。"
        )
        resp = await client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.3,
            max_tokens=200,
        )
        lesson = resp.content.strip()
        if lesson:
            return lesson
    except Exception:
        logger.warning("LLM gate lesson extraction failed, using raw comment", exc_info=True)

    # Fallback: use raw comment
    return comment if comment else summary
