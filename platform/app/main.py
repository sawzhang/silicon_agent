import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.api.webhooks import jira, gitlab
from app.config import settings
from app.db.init_db import init_db
from app.db.session import async_session_factory, engine
from app.integration.skillkit_bridge import init_bridge
from app.middleware.auth import JWTAuthMiddleware
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.services.agent_service import AgentService
from app.websocket.manager import ws_manager

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database tables...")
    await init_db(engine)

    # Seed default agents
    async with async_session_factory() as session:
        agent_service = AgentService(session)
        await agent_service.ensure_agents_exist()

    logger.info("Initializing SkillKit bridge...")
    await init_bridge(use_skillkit=settings.SKILLKIT_ENABLED)

    logger.info("Initializing WebSocket manager Redis connection...")
    await ws_manager.init_redis(settings.REDIS_URL)

    logger.info("Platform startup complete")
    yield
    # Shutdown
    logger.info("Platform shutting down")


app = FastAPI(
    title="SITC Agent Management Platform",
    description="Backend API for managing AI agent workforce",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware (order matters: outermost first)
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(JWTAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_v1_router)
app.include_router(jira.router)
app.include_router(gitlab.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Echo back for now; clients primarily receive broadcasts
            await ws_manager.send_to(websocket, "echo", {"message": data})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
