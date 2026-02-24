from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.agent_service import AgentService
from app.services.audit_service import AuditService
from app.services.circuit_breaker_service import CircuitBreakerService
from app.services.gate_service import GateService
from app.services.kpi_service import KPIService
from app.services.project_service import ProjectService
from app.services.skill_service import SkillService
from app.services.task_service import TaskService
from app.services.task_log_service import TaskLogService
from app.services.template_service import TemplateService


async def get_agent_service(
    session: AsyncSession = Depends(get_db),
) -> AgentService:
    return AgentService(session)


async def get_task_service(
    session: AsyncSession = Depends(get_db),
) -> TaskService:
    return TaskService(session)


async def get_gate_service(
    session: AsyncSession = Depends(get_db),
) -> GateService:
    return GateService(session)


async def get_skill_service(
    session: AsyncSession = Depends(get_db),
) -> SkillService:
    return SkillService(session)


async def get_kpi_service(
    session: AsyncSession = Depends(get_db),
) -> KPIService:
    return KPIService(session)


async def get_audit_service(
    session: AsyncSession = Depends(get_db),
) -> AuditService:
    return AuditService(session)


async def get_circuit_breaker_service(
    session: AsyncSession = Depends(get_db),
) -> CircuitBreakerService:
    return CircuitBreakerService(session)


async def get_template_service(
    session: AsyncSession = Depends(get_db),
) -> TemplateService:
    return TemplateService(session)


async def get_project_service(
    session: AsyncSession = Depends(get_db),
) -> ProjectService:
    return ProjectService(session)


async def get_task_log_service(
    session: AsyncSession = Depends(get_db),
) -> TaskLogService:
    return TaskLogService(session)
