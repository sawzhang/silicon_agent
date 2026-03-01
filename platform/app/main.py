import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.api.webhooks import github, gitlab, jira
from app.config import settings
from app.db.init_db import init_db
from app.db.session import async_session_factory, engine
from app.integration.skillkit_bridge import init_bridge
from app.integration.skillkit_env import hydrate_skillkit_env
from app.logging_config import setup_logging
from app.middleware.auth import JWTAuthMiddleware
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.request_logging import RequestLoggingMiddleware
from app.services.agent_service import AgentService
from app.services.task_log_pipeline import start_task_log_pipeline, stop_task_log_pipeline
from app.services.seed_service import seed_demo_data
from app.services.skill_sync_service import sync_skills_from_filesystem
from app.services.template_service import TemplateService
from app.websocket.manager import ws_manager
from app.worker import start_worker, stop_worker
from app.worker.agents import validate_role_tools_or_raise

setup_logging(debug=settings.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database tables...")
    await init_db(engine)

    applied_env = hydrate_skillkit_env(
        os.environ,
        llm_api_key=settings.LLM_API_KEY,
        llm_base_url=settings.LLM_BASE_URL,
        llm_model=settings.LLM_MODEL,
    )
    if applied_env:
        logger.info(
            "Hydrated SkillKit compatibility env from LLM settings: %s",
            sorted(applied_env.keys()),
        )

    # Seed default agents and templates
    async with async_session_factory() as session:
        agent_service = AgentService(session)
        await agent_service.ensure_agents_exist()

    async with async_session_factory() as session:
        template_service = TemplateService(session)
        await template_service.seed_builtin_templates()
        logger.info("Builtin task templates seeded")

    # Seed demo data (skills, gates, audit logs, sample tasks)
    async with async_session_factory() as session:
        await seed_demo_data(session)

    # Sync filesystem skill definitions → DB
    async with async_session_factory() as session:
        await sync_skills_from_filesystem(session)

    logger.info("Validating role tool policy against discovered SkillKit tools...")
    validate_role_tools_or_raise(fail_on_unknown=True)

    logger.info("Initializing SkillKit bridge...")
    await init_bridge(use_skillkit=settings.SKILLKIT_ENABLED)

    logger.info("Initializing WebSocket manager Redis connection...")
    await ws_manager.init_redis(settings.REDIS_URL)

    logger.info("Starting task log pipeline...")
    await start_task_log_pipeline()

    if settings.WORKER_ENABLED:
        if not settings.LLM_API_KEY:
            logger.warning(
                "WORKER_ENABLED=true but LLM_API_KEY is empty. "
                "Agent tasks will fail when calling LLM. Set LLM_API_KEY in .env"
            )
        logger.info("Starting worker...")
        await start_worker()

    logger.info("Platform startup complete")
    yield
    # Shutdown
    logger.info("Platform shutting down")
    await stop_worker()
    await stop_task_log_pipeline()
    from app.integration.notifier import close_notifier
    await close_notifier()
    if settings.SANDBOX_ENABLED:
        from app.worker.sandbox import close_sandbox_manager
        await close_sandbox_manager()


app = FastAPI(
    title="Silicon Agent Platform",
    description="Backend API for managing AI agent workforce",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware (order matters: outermost first)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(JWTAuthMiddleware)
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_v1_router)
app.include_router(jira.router)
app.include_router(gitlab.router)
app.include_router(github.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle ping/pong heartbeat from frontend
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws_manager.send_to(websocket, "pong", {})
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
