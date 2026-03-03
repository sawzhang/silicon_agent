from fastapi import APIRouter

from app.api.v1 import (
    agents,
    audit,
    auth,
    circuit_breaker,
    gates,
    integrations,
    kpi,
    llm_probe,
    projects,
    skills,
    task_logs,
    tasks,
    templates,
    triggers,
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth.router)
api_v1_router.include_router(agents.router)
api_v1_router.include_router(tasks.router)
api_v1_router.include_router(gates.router)
api_v1_router.include_router(skills.router)
api_v1_router.include_router(kpi.router)
api_v1_router.include_router(llm_probe.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(circuit_breaker.router)
api_v1_router.include_router(templates.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(task_logs.router)
api_v1_router.include_router(triggers.router)
api_v1_router.include_router(integrations.router)
