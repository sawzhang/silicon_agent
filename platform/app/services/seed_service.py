"""Seed demo data for Skills, Gates, Audit logs, and sample tasks."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLogModel
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.models.template import TaskTemplateModel

logger = logging.getLogger(__name__)

SEED_AUDIT_LOGS = [
    {"agent_role": "orchestrator", "action_type": "task_created", "risk_level": "low", "action_detail": {"task": "实现用户登录模块", "template": "full_pipeline"}},
    {"agent_role": "spec", "action_type": "spec_generated", "risk_level": "low", "action_detail": {"output": "技术方案已生成，包含3个接口设计"}},
    {"agent_role": "orchestrator", "action_type": "gate_created", "risk_level": "medium", "action_detail": {"gate_type": "spec_review", "reason": "方案需要人工审批"}},
    {"agent_role": "coding", "action_type": "code_generated", "risk_level": "low", "action_detail": {"files": 5, "lines": 320}},
    {"agent_role": "test", "action_type": "tests_executed", "risk_level": "low", "action_detail": {"total": 12, "passed": 11, "failed": 1}},
    {"agent_role": "coding", "action_type": "self_fix", "risk_level": "medium", "action_detail": {"reason": "测试失败自动修复", "attempt": 1}},
    {"agent_role": "test", "action_type": "tests_executed", "risk_level": "low", "action_detail": {"total": 12, "passed": 12, "failed": 0}},
    {"agent_role": "review", "action_type": "review_completed", "risk_level": "low", "action_detail": {"issues": 2, "severity": "minor"}},
    {"agent_role": "smoke", "action_type": "smoke_passed", "risk_level": "low", "action_detail": {"scenarios": 5, "passed": 5}},
    {"agent_role": "doc", "action_type": "doc_generated", "risk_level": "low", "action_detail": {"files": ["README.md", "CHANGELOG.md", "API.md"]}},
    {"agent_role": "orchestrator", "action_type": "task_completed", "risk_level": "low", "action_detail": {"duration_minutes": 8.5, "total_tokens": 45000}},
    {"agent_role": "orchestrator", "action_type": "task_created", "risk_level": "low", "action_detail": {"task": "修复支付回调bug", "template": "quick_fix"}},
    {"agent_role": "coding", "action_type": "code_generated", "risk_level": "medium", "action_detail": {"files": 2, "lines": 85, "touches_payment": True}},
    {"agent_role": "review", "action_type": "security_warning", "risk_level": "high", "action_detail": {"issue": "SQL注入风险", "file": "payment_handler.py", "line": 42}},
    {"agent_role": "coding", "action_type": "self_fix", "risk_level": "medium", "action_detail": {"reason": "安全审计修复SQL注入", "attempt": 1}},
]


async def seed_demo_data(session: AsyncSession) -> None:
    """Seed Skills, Gates, Audit logs, and sample tasks for demo."""

    # Check if already seeded (use TaskModel as marker — skills are synced from filesystem)
    task_count = await session.execute(select(TaskModel).limit(1))
    if task_count.scalar_one_or_none() is not None:
        logger.info("Demo data already seeded, skipping")
        return

    # --- Seed sample tasks with stages ---
    now = datetime.now(timezone.utc)

    # Get full_pipeline template
    tpl_result = await session.execute(
        select(TaskTemplateModel).where(TaskTemplateModel.name == "full_pipeline")
    )
    full_tpl = tpl_result.scalar_one_or_none()
    full_tpl_id = full_tpl.id if full_tpl else None

    # Get quick_fix template
    qf_result = await session.execute(
        select(TaskTemplateModel).where(TaskTemplateModel.name == "quick_fix")
    )
    quick_tpl = qf_result.scalar_one_or_none()
    quick_tpl_id = quick_tpl.id if quick_tpl else None

    sample_tasks = [
        {
            "title": "实现用户登录模块",
            "description": "支持邮箱+密码登录，OAuth2.0社交登录，JWT token管理",
            "status": "completed",
            "template_id": full_tpl_id,
            "total_tokens": 45200,
            "total_cost_rmb": 3.62,
            "created_at": now - timedelta(hours=6),
            "completed_at": now - timedelta(hours=5, minutes=30),
            "stages_status": "all_completed",
        },
        {
            "title": "修复支付回调bug",
            "description": "支付宝异步回调签名验证失败，需排查并修复",
            "status": "completed",
            "template_id": quick_tpl_id,
            "total_tokens": 12800,
            "total_cost_rmb": 1.02,
            "created_at": now - timedelta(hours=3),
            "completed_at": now - timedelta(hours=2, minutes=45),
            "stages_status": "all_completed",
        },
        {
            "title": "重构订单模块数据库层",
            "description": "将原始SQL替换为ORM，增加连接池和读写分离",
            "status": "running",
            "template_id": full_tpl_id,
            "total_tokens": 28500,
            "total_cost_rmb": 2.28,
            "created_at": now - timedelta(hours=1),
            "completed_at": None,
            "stages_status": "running_at_code",
        },
        {
            "title": "添加API限流中间件",
            "description": "基于令牌桶算法实现API限流，支持按用户和IP限制",
            "status": "pending",
            "template_id": full_tpl_id,
            "total_tokens": 0,
            "total_cost_rmb": 0.0,
            "created_at": now - timedelta(minutes=30),
            "completed_at": None,
            "stages_status": "all_pending",
        },
        {
            "title": "更新API接口文档",
            "description": "同步最新接口变更到OpenAPI文档",
            "status": "completed",
            "template_id": None,
            "total_tokens": 8900,
            "total_cost_rmb": 0.71,
            "created_at": now - timedelta(hours=8),
            "completed_at": now - timedelta(hours=7, minutes=50),
            "stages_status": "no_stages",
        },
    ]

    task_ids = []
    for task_data in sample_tasks:
        task_id = str(uuid.uuid4())
        task_ids.append(task_id)
        task = TaskModel(
            id=task_id,
            title=task_data["title"],
            description=task_data["description"],
            status=task_data["status"],
            template_id=task_data["template_id"],
            total_tokens=task_data["total_tokens"],
            total_cost_rmb=task_data["total_cost_rmb"],
            created_at=task_data["created_at"],
            completed_at=task_data["completed_at"],
        )
        session.add(task)

        # Create stages based on template
        if task_data["template_id"] and task_data["stages_status"] != "no_stages":
            tpl = full_tpl if task_data["template_id"] == full_tpl_id else quick_tpl
            if tpl:
                stage_defs = json.loads(tpl.stages)
                for i, sd in enumerate(stage_defs):
                    if task_data["stages_status"] == "all_completed":
                        s_status = "completed"
                    elif task_data["stages_status"] == "all_pending":
                        s_status = "pending"
                    elif task_data["stages_status"] == "running_at_code":
                        if sd["name"] in ("parse", "spec", "approve"):
                            s_status = "completed"
                        elif sd["name"] == "code":
                            s_status = "running"
                        else:
                            s_status = "pending"
                    else:
                        s_status = "pending"

                    stage = TaskStageModel(
                        task_id=task_id,
                        stage_name=sd["name"],
                        agent_role=sd["agent_role"],
                        status=s_status,
                    )
                    session.add(stage)

    logger.info("Seeded %d sample tasks", len(sample_tasks))

    # --- Seed Gates (linked to tasks) ---
    gates = [
        {
            "gate_type": "spec_review",
            "task_id": task_ids[0],  # 用户登录 - completed
            "agent_role": "spec",
            "status": "approved",
            "reviewer": "admin",
            "review_comment": "方案设计合理，同意执行",
            "content": {"stage": "spec", "summary": "技术方案包含3个接口设计"},
        },
        {
            "gate_type": "code_review",
            "task_id": task_ids[0],  # 用户登录 - completed
            "agent_role": "review",
            "status": "approved",
            "reviewer": "admin",
            "review_comment": "代码质量良好，2个minor问题已修复",
            "content": {"stage": "review", "issues": 2},
        },
        {
            "gate_type": "spec_review",
            "task_id": task_ids[2],  # 重构订单 - running
            "agent_role": "spec",
            "status": "approved",
            "reviewer": "admin",
            "review_comment": "数据库重构方案已审批通过",
            "content": {"stage": "spec", "summary": "ORM迁移+读写分离方案"},
        },
        {
            "gate_type": "final_signoff",
            "task_id": task_ids[2],  # 重构订单 - running, pending gate
            "agent_role": "orchestrator",
            "status": "pending",
            "content": {"stage": "signoff", "summary": "等待最终签收"},
        },
    ]

    review_time = now - timedelta(hours=5, minutes=45)
    for gate_data in gates:
        raw_content = gate_data.get("content")
        gate = HumanGateModel(
            gate_type=gate_data["gate_type"],
            task_id=gate_data["task_id"],
            agent_role=gate_data["agent_role"],
            status=gate_data["status"],
            content=json.dumps(raw_content, ensure_ascii=False) if isinstance(raw_content, dict) else raw_content,
            reviewer=gate_data.get("reviewer"),
            review_comment=gate_data.get("review_comment"),
            reviewed_at=review_time if gate_data["status"] != "pending" else None,
        )
        session.add(gate)
        review_time += timedelta(minutes=15)

    logger.info("Seeded %d gates", len(gates))

    # --- Seed Audit Logs ---
    log_time = now - timedelta(hours=6)
    for log_data in SEED_AUDIT_LOGS:
        detail = log_data["action_detail"]
        audit_log = AuditLogModel(
            agent_role=log_data["agent_role"],
            action_type=log_data["action_type"],
            action_detail=json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else detail,
            risk_level=log_data["risk_level"],
            created_at=log_time,
        )
        session.add(audit_log)
        log_time += timedelta(minutes=5)

    logger.info("Seeded %d audit logs", len(SEED_AUDIT_LOGS))

    await session.commit()
    logger.info("Demo seed data committed")
