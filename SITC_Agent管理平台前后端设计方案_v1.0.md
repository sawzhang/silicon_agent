# SITC Agent 管理平台 — 前后端技术设计方案 v1.0

> **版本**: 1.0
> **日期**: 2026-02-21
> **前置文档**: 《SkillKit适配分析与Agent管理平台设计方案 v1.0》第六章
> **定位**: 可直接指导开发的前后端详细设计，包含项目结构、核心代码、接口契约、组件设计

---

## 一、技术架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Nginx (反向代理)                              │
│                   静态资源 / API路由 / WebSocket升级                  │
├──────────────────────────────┬──────────────────────────────────────┤
│                              │                                      │
│   ┌──────────────────────┐   │   ┌──────────────────────────────┐   │
│   │   Frontend (SPA)     │   │   │   Backend (FastAPI)          │   │
│   │                      │   │   │                              │   │
│   │   React 18           │   │   │   /api/v1/*  REST API        │   │
│   │   Ant Design Pro 6   │   │   │   /ws       WebSocket        │   │
│   │   TypeScript 5       │   │   │   /webhooks Jira/GitLab      │   │
│   │   Zustand            │   │   │                              │   │
│   │   React Query        │   │   │   Python 3.11+               │   │
│   │   ECharts            │   │   │   SQLAlchemy 2.0             │   │
│   │                      │   │   │   Pydantic v2                │   │
│   └──────────┬───────────┘   │   └──────────┬───────────────────┘   │
│              │               │              │                       │
│              │ HTTP/WS       │              │                       │
│              └───────────────┼──────────────┘                       │
│                              │                                      │
├──────────────────────────────┴──────────────────────────────────────┤
│                        Infrastructure                               │
│   ┌────────────┐  ┌────────┐  ┌────────────┐  ┌─────────────────┐  │
│   │ PostgreSQL │  │ Redis  │  │ Prometheus │  │ SkillKit Core   │  │
│   │ 15+        │  │ 7+     │  │ + Grafana  │  │ (Python Import) │  │
│   └────────────┘  └────────┘  └────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 技术选型确认

| 层级 | 技术 | 版本 | 选型理由 |
|------|------|------|---------|
| **前端框架** | React | 18.x | Hooks生态成熟，团队熟悉 |
| **UI组件库** | Ant Design Pro | 6.x | 中后台开箱即用，ProTable/ProForm/ProLayout |
| **类型系统** | TypeScript | 5.x | 类型安全，接口契约前后端对齐 |
| **状态管理** | Zustand | 5.x | 轻量，适合中等复杂度状态 |
| **数据请求** | React Query (TanStack) | 5.x | 自动缓存/重试/轮询，WebSocket集成 |
| **图表** | ECharts | 5.x | KPI趋势图/饼图/柱状图 |
| **后端框架** | FastAPI | 0.115+ | 原生async，自动OpenAPI文档，与SkillKit同生态 |
| **ORM** | SQLAlchemy | 2.0 | async支持，类型映射成熟 |
| **数据验证** | Pydantic | v2 | FastAPI原生集成，高性能 |
| **数据库** | PostgreSQL | 15+ | JSONB/数组类型/窗口函数 |
| **缓存/队列** | Redis | 7+ | Pub/Sub（WebSocket广播）+ 缓存 |
| **任务队列** | Celery（可选） | 5.x | 定时报告生成、异步通知 |

---

## 二、后端设计

### 2.1 项目结构

```
agent-platform/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI应用入口
│   ├── config.py                   # 配置管理（环境变量/YAML）
│   ├── dependencies.py             # 依赖注入
│   │
│   ├── api/                        # API路由层
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py           # v1总路由
│   │   │   ├── agents.py           # Agent管理 API
│   │   │   ├── tasks.py            # 任务管理 API
│   │   │   ├── skills.py           # Skills管理 API
│   │   │   ├── gates.py            # 审批管理 API
│   │   │   ├── kpi.py              # KPI查询 API
│   │   │   ├── audit.py            # 审计日志 API
│   │   │   └── circuit_breaker.py  # 止损控制 API
│   │   └── webhooks/
│   │       ├── __init__.py
│   │       ├── jira.py             # Jira Webhook接收
│   │       └── gitlab.py           # GitLab Webhook接收
│   │
│   ├── schemas/                    # Pydantic请求/响应模型
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── task.py
│   │   ├── skill.py
│   │   ├── gate.py
│   │   ├── kpi.py
│   │   ├── audit.py
│   │   └── common.py               # 分页/排序/通用响应
│   │
│   ├── models/                     # SQLAlchemy ORM模型
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── task.py
│   │   ├── skill.py
│   │   ├── gate.py
│   │   ├── kpi.py
│   │   └── audit.py
│   │
│   ├── services/                   # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── agent_service.py        # Agent生命周期管理
│   │   ├── task_service.py         # 任务调度与状态机
│   │   ├── skill_service.py        # Skills CRUD + Git同步
│   │   ├── gate_service.py         # 审批流程引擎
│   │   ├── kpi_service.py          # KPI聚合与报告
│   │   ├── audit_service.py        # 审计日志记录与查询
│   │   └── circuit_breaker_service.py  # 三级止损状态机
│   │
│   ├── integration/                # SkillKit集成层
│   │   ├── __init__.py
│   │   ├── skillkit_bridge.py      # AgentRunner/SkillsEngine桥接
│   │   ├── event_collector.py      # EventBus事件采集→DB/Redis
│   │   └── session_reader.py       # SessionManager数据读取
│   │
│   ├── websocket/                  # WebSocket管理
│   │   ├── __init__.py
│   │   ├── manager.py              # 连接管理 + Redis Pub/Sub
│   │   └── events.py               # WS事件类型定义
│   │
│   ├── middleware/                  # 中间件
│   │   ├── __init__.py
│   │   ├── auth.py                 # JWT认证
│   │   ├── logging.py              # 请求日志
│   │   └── error_handler.py        # 全局异常处理
│   │
│   └── db/                         # 数据库
│       ├── __init__.py
│       ├── session.py              # async SessionLocal
│       └── migrations/             # Alembic迁移
│           ├── env.py
│           └── versions/
│
├── tests/
│   ├── conftest.py
│   ├── test_agents.py
│   ├── test_tasks.py
│   └── ...
│
├── alembic.ini
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

### 2.2 应用入口与中间件

```python
# app/main.py

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.webhooks import jira, gitlab
from app.websocket.manager import ws_manager
from app.middleware.auth import JWTAuthMiddleware
from app.middleware.error_handler import error_handler_middleware
from app.integration.skillkit_bridge import SkillKitBridge
from app.db.session import engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化SkillKit桥接，关闭时清理"""
    # 启动
    app.state.skillkit = SkillKitBridge()
    await app.state.skillkit.initialize()
    await ws_manager.connect_redis()

    yield

    # 关闭
    await app.state.skillkit.shutdown()
    await ws_manager.disconnect_redis()

app = FastAPI(
    title="SITC Agent Management Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# 中间件（按注册逆序执行）
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
app.middleware("http")(error_handler_middleware)
app.add_middleware(JWTAuthMiddleware)

# 路由
app.include_router(api_router, prefix="/api/v1")
app.include_router(jira.router, prefix="/webhooks/jira")
app.include_router(gitlab.router, prefix="/webhooks/gitlab")

# WebSocket
@app.websocket("/ws")
async def websocket_endpoint(websocket):
    await ws_manager.handle(websocket)
```

### 2.3 SkillKit 集成层（核心桥接）

```python
# app/integration/skillkit_bridge.py

from typing import Dict, Optional
from skillkit import AgentRunner, SkillsEngine, EventBus
from skillkit.config import AgentConfig, SkillsConfig
from skillkit.events import (
    AGENT_START, AGENT_END, TURN_START, TURN_END,
    BEFORE_TOOL_CALL, AFTER_TOOL_RESULT
)
from app.integration.event_collector import EventCollector

# Agent角色枚举
AGENT_ROLES = [
    "orchestrator", "spec", "coding", "test", "review", "smoke", "doc"
]

# 角色→默认模型映射
ROLE_MODEL_MAP = {
    "orchestrator": "claude-opus-4",
    "spec":         "claude-opus-4",
    "coding":       "claude-sonnet-4",
    "test":         "claude-sonnet-4",
    "review":       "claude-opus-4",
    "smoke":        "claude-sonnet-4",
    "doc":          "claude-sonnet-4",
}

class SkillKitBridge:
    """
    管理平台与SkillKit的桥接层。

    职责：
    1. 管理7个AgentRunner实例的生命周期
    2. 在每个AgentRunner的EventBus上注册事件采集器
    3. 提供Agent状态查询接口
    4. 转发SkillsEngine的技能数据
    """

    def __init__(self):
        self._runners: Dict[str, AgentRunner] = {}
        self._engines: Dict[str, SkillsEngine] = {}
        self._collector = EventCollector()

    async def initialize(self):
        """应用启动时初始化（不自动创建Runner，按需创建）"""
        await self._collector.initialize()

    async def get_or_create_runner(
        self, role: str, config_override: Optional[dict] = None
    ) -> AgentRunner:
        """获取或创建指定角色的AgentRunner"""
        if role in self._runners:
            return self._runners[role]

        # 构建配置
        model = ROLE_MODEL_MAP.get(role, "claude-sonnet-4")
        skill_dirs = [
            "/skills/L1-common",
            "/skills/L2-domain",
            "/skills/L3-sprint",
        ]

        runner = AgentRunner.create(
            model=model,
            skill_dirs=skill_dirs,
            system_prompt=self._build_role_prompt(role),
            **(config_override or {})
        )

        # 注册事件采集
        self._register_event_handlers(role, runner)

        self._runners[role] = runner
        self._engines[role] = runner.skills_engine
        return runner

    def _register_event_handlers(self, role: str, runner: AgentRunner):
        """在AgentRunner的EventBus上注册平台级事件处理"""
        bus = runner.events

        bus.on(AGENT_START, lambda e: self._collector.on_agent_start(role, e))
        bus.on(AGENT_END, lambda e: self._collector.on_agent_end(role, e))
        bus.on(AFTER_TOOL_RESULT, lambda e: self._collector.on_tool_result(role, e))
        bus.on(BEFORE_TOOL_CALL, lambda e: self._collector.on_before_tool(role, e))

    def get_agent_status(self, role: str) -> dict:
        """查询Agent运行状态"""
        runner = self._runners.get(role)
        if not runner:
            return {"role": role, "status": "stopped"}

        snapshot = runner.snapshot
        return {
            "role": role,
            "status": "running" if not runner._abort_signal.is_set() else "idle",
            "model": runner.config.model,
            "skills_loaded": snapshot.skill_names if snapshot else [],
            "cumulative_usage": runner.cumulative_usage.__dict__
                if runner.cumulative_usage else None,
        }

    def get_all_status(self) -> list:
        """查询所有Agent状态"""
        return [self.get_agent_status(role) for role in AGENT_ROLES]

    def get_skills(self, role: str) -> list:
        """获取指定角色可用的Skills列表"""
        engine = self._engines.get(role)
        if not engine:
            return []
        snapshot = engine.get_snapshot()
        return [
            {
                "name": s.name,
                "description": s.description,
                "tags": s.metadata.get("tags", []),
                "model": s.model,
                "context": s.context,
            }
            for s in snapshot.skills
        ]

    async def shutdown(self):
        """关闭所有AgentRunner"""
        for role, runner in self._runners.items():
            runner.abort()
        self._runners.clear()
        self._engines.clear()

    def _build_role_prompt(self, role: str) -> str:
        """构建角色专属System Prompt（简化示例）"""
        prompts = {
            "orchestrator": "你是编排官，负责解析需求、分解任务、调度Agent。",
            "spec":         "你是方案官，负责生成Implementation Plan。",
            "coding":       "你是研发官，负责在Plan范围内编写代码。",
            "test":         "你是测试官，负责生成测试、驱动自修复循环。",
            "review":       "你是审计官，负责安全/性能/规范三维审计。",
            "smoke":        "你是巡检官，负责端到端集成验证。",
            "doc":          "你是文档官，负责生成Changelog、更新Wiki、提炼Skills。",
        }
        return prompts.get(role, "")
```

### 2.4 事件采集器

```python
# app/integration/event_collector.py

from datetime import datetime
from skillkit.events import (
    AgentStartEvent, AgentEndEvent,
    AfterToolResultEvent, BeforeToolCallEvent
)
from app.websocket.manager import ws_manager
from app.db.session import async_session
from app.models.audit import AuditLog
from app.models.kpi import KPIMetric

class EventCollector:
    """
    从SkillKit EventBus采集事件，写入DB + 推送WebSocket。

    数据流：EventBus → EventCollector → {DB, Redis Pub/Sub → WebSocket}
    """

    async def initialize(self):
        pass

    async def on_agent_start(self, role: str, event: AgentStartEvent):
        """Agent开始执行"""
        await ws_manager.broadcast({
            "type": "agent_status",
            "role": role,
            "status": "running",
            "model": event.model,
            "timestamp": datetime.now().isoformat(),
        })

    async def on_agent_end(self, role: str, event: AgentEndEvent):
        """Agent执行结束 — 采集Agent级KPI"""
        # 推送状态变更
        await ws_manager.broadcast({
            "type": "agent_status",
            "role": role,
            "status": "idle",
            "total_turns": event.total_turns,
            "finish_reason": event.finish_reason,
            "timestamp": datetime.now().isoformat(),
        })

        # 写入KPI
        async with async_session() as session:
            session.add(KPIMetric(
                metric_name="agent_turns",
                agent_role=role,
                value=event.total_turns,
                unit="count",
            ))
            await session.commit()

    async def on_tool_result(self, role: str, event: AfterToolResultEvent):
        """工具执行完成 — 采集工具级KPI + 推送实时活动"""
        # 推送实时活动
        await ws_manager.broadcast({
            "type": "activity",
            "role": role,
            "tool": event.tool_name,
            "args_summary": self._summarize_args(event.tool_name, event.args),
            "timestamp": datetime.now().isoformat(),
        })

        # 写入审计日志
        async with async_session() as session:
            session.add(AuditLog(
                agent_role=role,
                action_type="tool_call",
                action_detail={
                    "tool": event.tool_name,
                    "args": self._safe_args(event.args),
                    "result_length": len(str(event.result)) if event.result else 0,
                },
                risk_level=self._assess_risk(event.tool_name, event.args),
            ))
            await session.commit()

    async def on_before_tool(self, role: str, event: BeforeToolCallEvent):
        """工具执行前 — 推送正在执行的操作"""
        await ws_manager.broadcast({
            "type": "tool_executing",
            "role": role,
            "tool": event.tool_name,
            "timestamp": datetime.now().isoformat(),
        })

    def _summarize_args(self, tool: str, args: dict) -> str:
        if tool == "execute":
            cmd = args.get("command", "")
            return cmd[:80] + "..." if len(cmd) > 80 else cmd
        elif tool in ("read", "write"):
            return args.get("path", "unknown")
        elif tool == "skill":
            return f"/{args.get('name', 'unknown')}"
        return str(args)[:80]

    def _safe_args(self, args: dict) -> dict:
        """过滤敏感参数（Token/Key等）"""
        safe = {}
        for k, v in args.items():
            if any(s in k.lower() for s in ("token", "key", "secret", "password")):
                safe[k] = "***REDACTED***"
            else:
                safe[k] = str(v)[:500]
        return safe

    def _assess_risk(self, tool: str, args: dict) -> str:
        if tool == "execute":
            cmd = args.get("command", "")
            if any(k in cmd for k in ("rm -rf", "drop table", "force push")):
                return "critical"
            if any(k in cmd for k in ("git push", "docker", "deploy")):
                return "high"
            return "medium"
        if tool == "write":
            return "medium"
        return "low"
```

### 2.5 WebSocket 管理

```python
# app/websocket/manager.py

import json
from typing import Set
from fastapi import WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis

REDIS_CHANNEL = "agent_platform:events"

class WebSocketManager:
    """
    WebSocket连接管理器。

    架构：
    - 前端通过 ws://host/ws 建立连接
    - 后端EventCollector发布事件到Redis Pub/Sub
    - Manager订阅Redis，广播到所有WebSocket连接
    - 支持多实例部署（通过Redis共享事件）
    """

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._redis: aioredis.Redis = None
        self._pubsub = None

    async def connect_redis(self):
        from app.config import settings
        self._redis = aioredis.from_url(settings.redis_url)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(REDIS_CHANNEL)
        # 启动后台监听
        import asyncio
        asyncio.create_task(self._listen_redis())

    async def disconnect_redis(self):
        if self._pubsub:
            await self._pubsub.unsubscribe(REDIS_CHANNEL)
        if self._redis:
            await self._redis.aclose()

    async def handle(self, websocket: WebSocket):
        """处理WebSocket连接"""
        await websocket.accept()
        self._connections.add(websocket)
        try:
            while True:
                # 接收前端消息（心跳/订阅过滤等）
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            self._connections.discard(websocket)

    async def broadcast(self, event: dict):
        """发布事件 → Redis → 所有WebSocket"""
        if self._redis:
            await self._redis.publish(REDIS_CHANNEL, json.dumps(event))

    async def _listen_redis(self):
        """后台监听Redis，转发到WebSocket"""
        async for message in self._pubsub.listen():
            if message["type"] == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                dead = set()
                for ws in self._connections:
                    try:
                        await ws.send_text(data)
                    except Exception:
                        dead.add(ws)
                self._connections -= dead

ws_manager = WebSocketManager()
```

### 2.6 API 路由层示例

#### Agent 管理 API

```python
# app/api/v1/agents.py

from fastapi import APIRouter, Depends, Request
from app.schemas.agent import (
    AgentStatusResponse, AgentConfigUpdate,
    AgentListResponse, AgentSessionResponse
)
from app.services.agent_service import AgentService
from app.dependencies import get_agent_service

router = APIRouter(prefix="/agents", tags=["agents"])

@router.get("", response_model=AgentListResponse)
async def list_agents(request: Request):
    """获取所有Agent状态"""
    bridge = request.app.state.skillkit
    statuses = bridge.get_all_status()
    return {"agents": statuses}

@router.get("/{role}", response_model=AgentStatusResponse)
async def get_agent(role: str, request: Request):
    """获取指定角色Agent详情"""
    bridge = request.app.state.skillkit
    return bridge.get_agent_status(role)

@router.put("/{role}/config")
async def update_config(
    role: str, body: AgentConfigUpdate,
    service: AgentService = Depends(get_agent_service)
):
    """更新Agent配置（模型/Skills/超参数）"""
    return await service.update_config(role, body)

@router.post("/{role}/start")
async def start_agent(
    role: str, request: Request,
    service: AgentService = Depends(get_agent_service)
):
    """启动Agent"""
    bridge = request.app.state.skillkit
    runner = await bridge.get_or_create_runner(role)
    await service.mark_running(role)
    return {"status": "started", "role": role}

@router.post("/{role}/stop")
async def stop_agent(role: str, request: Request):
    """停止Agent"""
    bridge = request.app.state.skillkit
    runner = bridge._runners.get(role)
    if runner:
        runner.abort()
    return {"status": "stopped", "role": role}

@router.get("/{role}/session", response_model=AgentSessionResponse)
async def get_session(
    role: str,
    service: AgentService = Depends(get_agent_service)
):
    """获取Agent当前会话记录"""
    return await service.get_current_session(role)
```

#### 任务管理 API

```python
# app/api/v1/tasks.py

from uuid import UUID
from fastapi import APIRouter, Depends, Query
from app.schemas.task import (
    TaskListResponse, TaskDetailResponse,
    TaskCreateRequest, TaskStageResponse
)
from app.schemas.common import PaginationParams
from app.services.task_service import TaskService
from app.dependencies import get_task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status: str = Query(None, description="pending/in_progress/completed/failed"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    service: TaskService = Depends(get_task_service)
):
    """任务列表（分页/筛选）"""
    return await service.list_tasks(status=status, page=page, size=size)

@router.post("", response_model=TaskDetailResponse, status_code=201)
async def create_task(
    body: TaskCreateRequest,
    service: TaskService = Depends(get_task_service)
):
    """手动创建任务"""
    return await service.create_task(body)

@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: UUID,
    service: TaskService = Depends(get_task_service)
):
    """任务详情"""
    return await service.get_task(task_id)

@router.get("/{task_id}/stages", response_model=list[TaskStageResponse])
async def get_stages(
    task_id: UUID,
    service: TaskService = Depends(get_task_service)
):
    """任务Pipeline各阶段"""
    return await service.get_stages(task_id)

@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    service: TaskService = Depends(get_task_service)
):
    """取消任务"""
    return await service.cancel_task(task_id)
```

#### 审批管理 API

```python
# app/api/v1/gates.py

from uuid import UUID
from fastapi import APIRouter, Depends, Query
from app.schemas.gate import (
    GateListResponse, GateDetailResponse,
    GateApproveRequest, GateRejectRequest
)
from app.services.gate_service import GateService
from app.dependencies import get_gate_service

router = APIRouter(prefix="/gates", tags=["gates"])

@router.get("", response_model=GateListResponse)
async def list_pending_gates(
    status: str = Query("pending"),
    service: GateService = Depends(get_gate_service)
):
    """待审批列表"""
    return await service.list_gates(status=status)

@router.get("/{gate_id}", response_model=GateDetailResponse)
async def get_gate(
    gate_id: UUID,
    service: GateService = Depends(get_gate_service)
):
    """审批项详情"""
    return await service.get_gate(gate_id)

@router.post("/{gate_id}/approve")
async def approve_gate(
    gate_id: UUID,
    body: GateApproveRequest,
    service: GateService = Depends(get_gate_service)
):
    """通过审批 → 解除HumanGate阻塞 → Agent继续执行"""
    return await service.approve(gate_id, body.reviewer, body.comment)

@router.post("/{gate_id}/reject")
async def reject_gate(
    gate_id: UUID,
    body: GateRejectRequest,
    service: GateService = Depends(get_gate_service)
):
    """拒绝审批 → 打回Agent重新执行"""
    return await service.reject(gate_id, body.reviewer, body.reason)

@router.get("/history", response_model=GateListResponse)
async def gate_history(
    days: int = Query(7, ge=1, le=90),
    service: GateService = Depends(get_gate_service)
):
    """审批历史"""
    return await service.get_history(days=days)
```

#### Skills 管理 API

```python
# app/api/v1/skills.py

from fastapi import APIRouter, Depends, Query
from app.schemas.skill import (
    SkillListResponse, SkillDetailResponse,
    SkillCreateRequest, SkillUpdateRequest,
    SkillVersionListResponse, SkillStatsResponse
)
from app.services.skill_service import SkillService
from app.dependencies import get_skill_service

router = APIRouter(prefix="/skills", tags=["skills"])

@router.get("", response_model=SkillListResponse)
async def list_skills(
    layer: str = Query(None, description="L1/L2/L3"),
    tag: str = Query(None),
    role: str = Query(None, description="适用角色"),
    status: str = Query("active"),
    service: SkillService = Depends(get_skill_service)
):
    """Skills列表"""
    return await service.list_skills(
        layer=layer, tag=tag, role=role, status=status
    )

@router.post("", response_model=SkillDetailResponse, status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    service: SkillService = Depends(get_skill_service)
):
    """创建新Skill（写入Git + 同步DB）"""
    return await service.create_skill(body)

@router.get("/stats", response_model=SkillStatsResponse)
async def skill_stats(
    service: SkillService = Depends(get_skill_service)
):
    """Skills使用统计"""
    return await service.get_stats()

@router.get("/{name}", response_model=SkillDetailResponse)
async def get_skill(
    name: str,
    service: SkillService = Depends(get_skill_service)
):
    """Skill详情"""
    return await service.get_skill(name)

@router.put("/{name}")
async def update_skill(
    name: str, body: SkillUpdateRequest,
    service: SkillService = Depends(get_skill_service)
):
    """更新Skill内容（新版本写入Git + 同步DB）"""
    return await service.update_skill(name, body)

@router.delete("/{name}")
async def archive_skill(
    name: str,
    service: SkillService = Depends(get_skill_service)
):
    """归档Skill"""
    return await service.archive_skill(name)

@router.get("/{name}/versions", response_model=SkillVersionListResponse)
async def skill_versions(
    name: str,
    service: SkillService = Depends(get_skill_service)
):
    """版本历史"""
    return await service.get_versions(name)

@router.post("/{name}/rollback")
async def rollback_skill(
    name: str, version: str = Query(...),
    service: SkillService = Depends(get_skill_service)
):
    """回滚到指定版本"""
    return await service.rollback(name, version)
```

#### KPI 查询 API

```python
# app/api/v1/kpi.py

from fastapi import APIRouter, Depends, Query
from app.schemas.kpi import (
    KPISummaryResponse, KPITimeSeriesResponse, KPIReportResponse
)
from app.services.kpi_service import KPIService
from app.dependencies import get_kpi_service

router = APIRouter(prefix="/kpi", tags=["kpi"])

@router.get("/summary", response_model=KPISummaryResponse)
async def kpi_summary(
    period: str = Query("week", description="day/week/month"),
    service: KPIService = Depends(get_kpi_service)
):
    """KPI概览（当前值 + 趋势 + 达标状态）"""
    return await service.get_summary(period)

@router.get("/metrics/{name}", response_model=KPITimeSeriesResponse)
async def kpi_timeseries(
    name: str,
    days: int = Query(7, ge=1, le=90),
    service: KPIService = Depends(get_kpi_service)
):
    """特定指标时序数据"""
    return await service.get_timeseries(name, days)

@router.get("/report", response_model=KPIReportResponse)
async def kpi_report(
    type: str = Query("weekly", description="weekly/monthly"),
    service: KPIService = Depends(get_kpi_service)
):
    """生成周/月报告"""
    return await service.generate_report(type)

@router.get("/compare")
async def psp_compare(
    months: int = Query(3, ge=1, le=12),
    service: KPIService = Depends(get_kpi_service)
):
    """Token成本 vs PSP成本对比"""
    return await service.psp_compare(months)
```

### 2.7 Pydantic Schema 定义

```python
# app/schemas/common.py

from datetime import datetime
from typing import Optional, Generic, TypeVar, List
from pydantic import BaseModel
from uuid import UUID

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    size: int
    pages: int
```

```python
# app/schemas/agent.py

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    thinking_tokens: int = 0
    total_cost_rmb: float = 0.0

class AgentStatusResponse(BaseModel):
    role: str
    status: str                          # running/idle/waiting/error/stopped
    model: Optional[str] = None
    skills_loaded: List[str] = []
    current_task_id: Optional[str] = None
    cumulative_usage: Optional[TokenUsage] = None
    started_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None

class AgentListResponse(BaseModel):
    agents: List[AgentStatusResponse]

class AgentConfigUpdate(BaseModel):
    model: Optional[str] = None          # claude-opus-4 / claude-sonnet-4
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_turns: Optional[int] = None
    thinking_level: Optional[str] = None # off/low/medium/high
    extra_skill_dirs: Optional[List[str]] = None
    system_prompt_append: Optional[str] = None

class AgentSessionResponse(BaseModel):
    session_id: str
    entries: List[dict]                  # SessionManager的JSONL条目
    total_entries: int
```

```python
# app/schemas/task.py

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
from uuid import UUID

class TaskCreateRequest(BaseModel):
    jira_id: str
    title: str
    description: Optional[str] = None

class TaskStageResponse(BaseModel):
    id: UUID
    stage_name: str         # parse/spec/approve/code/test/review/smoke/doc/signoff
    agent_role: str
    status: str             # pending/running/completed/failed/blocked
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    tokens_used: int = 0
    turns_used: int = 0
    self_fix_count: int = 0

class TaskDetailResponse(BaseModel):
    id: UUID
    jira_id: str
    title: str
    description: Optional[str] = None
    status: str
    stages: List[TaskStageResponse] = []
    created_at: datetime
    completed_at: Optional[datetime] = None
    total_tokens: int = 0
    total_cost_rmb: float = 0.0

class TaskListResponse(BaseModel):
    items: List[TaskDetailResponse]
    total: int
    page: int
    size: int
```

```python
# app/schemas/gate.py

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
from uuid import UUID

class GateDetailResponse(BaseModel):
    id: UUID
    gate_type: str          # spec_approval / review_confirm / final_signoff
    task_id: UUID
    task_title: str
    agent_role: str
    content: dict           # 待审批内容（Plan/Review结果等）
    status: str             # pending/approved/rejected/timeout
    reviewer: Optional[str] = None
    review_comment: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    waiting_minutes: float  # 已等待时间

class GateListResponse(BaseModel):
    items: List[GateDetailResponse]
    total: int

class GateApproveRequest(BaseModel):
    reviewer: str
    comment: Optional[str] = None

class GateRejectRequest(BaseModel):
    reviewer: str
    reason: str
```

```python
# app/schemas/kpi.py

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

class KPIMetricValue(BaseModel):
    name: str               # fpr / self_fix_rate / coverage / ...
    display_name: str       # 中文显示名
    current_value: float
    target: float
    warning_line: float
    unit: str               # percent / minutes / count / currency
    status: str             # ok / warning / critical
    trend: str              # up / down / flat
    trend_value: float      # 变化量

class KPISummaryResponse(BaseModel):
    period: str
    metrics: List[KPIMetricValue]
    total_tasks_completed: int
    total_tokens_used: int
    total_cost_rmb: float
    psp_equivalent_rmb: float
    savings_rate: float

class KPITimeSeriesPoint(BaseModel):
    timestamp: datetime
    value: float

class KPITimeSeriesResponse(BaseModel):
    metric_name: str
    points: List[KPITimeSeriesPoint]
    avg: float
    min: float
    max: float

class KPIReportResponse(BaseModel):
    type: str               # weekly / monthly
    period_start: datetime
    period_end: datetime
    summary: str
    metrics: List[KPIMetricValue]
    highlights: List[str]
    risks: List[str]
```

### 2.8 Service 层核心逻辑

```python
# app/services/gate_service.py — 审批服务（最关键的业务逻辑之一）

from uuid import UUID
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gate import HumanGateModel
from app.models.task import TaskStageModel
from app.websocket.manager import ws_manager

class GateService:
    def __init__(self, db: AsyncSession, skillkit_bridge):
        self.db = db
        self.bridge = skillkit_bridge

    async def approve(self, gate_id: UUID, reviewer: str, comment: str = ""):
        """
        审批通过：
        1. 更新DB记录
        2. 解除SkillKit HumanGate阻塞 → Agent继续执行
        3. WebSocket推送审批结果
        """
        gate = await self._get_gate(gate_id)
        gate.status = "approved"
        gate.reviewer = reviewer
        gate.review_comment = comment
        gate.reviewed_at = datetime.now()

        # 关键：解除对应HumanGate的asyncio.Event阻塞
        workforce = self.bridge.get_workforce_orchestrator()
        if workforce and gate_id_str in workforce.human_gates:
            workforce.human_gates[str(gate_id)].approve(reviewer, comment)

        await self.db.commit()

        # 推送WebSocket
        await ws_manager.broadcast({
            "type": "gate_resolved",
            "gate_id": str(gate_id),
            "gate_type": gate.gate_type,
            "action": "approved",
            "reviewer": reviewer,
        })

        return {"status": "approved"}

    async def reject(self, gate_id: UUID, reviewer: str, reason: str):
        """审批拒绝 → Agent打回重新执行"""
        gate = await self._get_gate(gate_id)
        gate.status = "rejected"
        gate.reviewer = reviewer
        gate.review_comment = reason
        gate.reviewed_at = datetime.now()

        workforce = self.bridge.get_workforce_orchestrator()
        if workforce and str(gate_id) in workforce.human_gates:
            workforce.human_gates[str(gate_id)].reject(reviewer, reason)

        await self.db.commit()

        await ws_manager.broadcast({
            "type": "gate_resolved",
            "gate_id": str(gate_id),
            "gate_type": gate.gate_type,
            "action": "rejected",
            "reviewer": reviewer,
        })

        return {"status": "rejected", "reason": reason}

    async def list_gates(self, status: str = "pending"):
        stmt = (
            select(HumanGateModel)
            .where(HumanGateModel.status == status)
            .order_by(HumanGateModel.created_at.desc())
        )
        result = await self.db.execute(stmt)
        gates = result.scalars().all()
        return {
            "items": [self._to_response(g) for g in gates],
            "total": len(gates),
        }

    async def _get_gate(self, gate_id: UUID) -> HumanGateModel:
        stmt = select(HumanGateModel).where(HumanGateModel.id == gate_id)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    def _to_response(self, gate: HumanGateModel) -> dict:
        waiting = (datetime.now() - gate.created_at).total_seconds() / 60
        return {
            "id": gate.id,
            "gate_type": gate.gate_type,
            "task_id": gate.task_id,
            "status": gate.status,
            "reviewer": gate.reviewer,
            "review_comment": gate.review_comment,
            "created_at": gate.created_at,
            "reviewed_at": gate.reviewed_at,
            "waiting_minutes": round(waiting, 1),
        }
```

### 2.9 Webhook 接收

```python
# app/api/webhooks/jira.py

from fastapi import APIRouter, Request, Header, HTTPException
from app.services.task_service import TaskService

router = APIRouter(tags=["webhooks"])

@router.post("")
async def handle_jira_webhook(
    request: Request,
    x_atlassian_token: str = Header(None),
):
    """
    Jira Webhook接收器。

    触发条件：Issue创建/更新，且包含 "agent-task" 标签。
    处理流程：解析Payload → 创建Task → WorkforceOrchestrator.dispatch()
    """
    payload = await request.json()

    event = payload.get("webhookEvent", "")
    issue = payload.get("issue", {})
    labels = [l.get("name") for l in issue.get("fields", {}).get("labels", [])]

    # 仅处理带 agent-task 标签的Issue
    if "agent-task" not in labels:
        return {"status": "ignored", "reason": "no agent-task label"}

    if event in ("jira:issue_created", "jira:issue_updated"):
        fields = issue.get("fields", {})
        task_service: TaskService = request.app.state.task_service

        task = await task_service.create_task_from_jira(
            jira_id=issue.get("key"),
            title=fields.get("summary", ""),
            description=fields.get("description", ""),
            acceptance_criteria=fields.get("customfield_10001", ""),
            priority=fields.get("priority", {}).get("name", "Medium"),
        )

        # 触发Agent集群处理
        bridge = request.app.state.skillkit
        orchestrator = bridge.get_workforce_orchestrator()
        if orchestrator:
            import asyncio
            asyncio.create_task(orchestrator.dispatch_task(task))

        return {"status": "dispatched", "task_id": str(task.id)}

    return {"status": "ignored", "reason": f"unhandled event: {event}"}
```

---

## 三、前端设计

### 3.1 项目结构

```
agent-platform-web/
├── public/
│   └── favicon.ico
├── src/
│   ├── main.tsx                        # 入口
│   ├── App.tsx                         # 根组件 + 路由
│   ├── vite-env.d.ts
│   │
│   ├── layouts/
│   │   └── BasicLayout.tsx             # ProLayout主布局（侧边栏+顶栏）
│   │
│   ├── pages/                          # 页面（按功能模块）
│   │   ├── Dashboard/
│   │   │   └── index.tsx               # 集群总览页
│   │   ├── Tasks/
│   │   │   ├── index.tsx               # 任务列表
│   │   │   └── TaskDetail.tsx          # 任务Pipeline详情
│   │   ├── Gates/
│   │   │   └── index.tsx               # 审批中心
│   │   ├── Skills/
│   │   │   ├── index.tsx               # Skills列表
│   │   │   ├── SkillDetail.tsx         # Skill详情+版本
│   │   │   └── SkillEditor.tsx         # Skill编辑器（Markdown）
│   │   ├── KPI/
│   │   │   └── index.tsx               # KPI Dashboard
│   │   ├── Audit/
│   │   │   └── index.tsx               # 审计日志
│   │   ├── Config/
│   │   │   └── index.tsx               # Agent配置
│   │   └── CircuitBreaker/
│   │       └── index.tsx               # 止损控制台
│   │
│   ├── components/                     # 通用组件
│   │   ├── AgentCard/
│   │   │   └── index.tsx               # Agent状态卡片
│   │   ├── PipelineView/
│   │   │   └── index.tsx               # Pipeline可视化（Steps组件）
│   │   ├── ActivityFeed/
│   │   │   └── index.tsx               # 实时活动流
│   │   ├── KPICard/
│   │   │   └── index.tsx               # KPI指标卡片
│   │   ├── KPIChart/
│   │   │   └── index.tsx               # KPI趋势图（ECharts）
│   │   ├── GateApprovalCard/
│   │   │   └── index.tsx               # 审批卡片
│   │   ├── SkillMarkdownEditor/
│   │   │   └── index.tsx               # Skill Markdown编辑器
│   │   └── CostAnalysis/
│   │       └── index.tsx               # Token成本分析图
│   │
│   ├── hooks/                          # 自定义Hooks
│   │   ├── useWebSocket.ts             # WebSocket连接 + 自动重连
│   │   ├── useAgents.ts                # Agent数据查询
│   │   ├── useTasks.ts                 # Task数据查询
│   │   ├── useGates.ts                 # 审批数据查询
│   │   ├── useKPI.ts                   # KPI数据查询
│   │   └── useSkills.ts               # Skills数据查询
│   │
│   ├── stores/                         # Zustand状态管理
│   │   ├── agentStore.ts               # Agent实时状态（WebSocket驱动）
│   │   ├── activityStore.ts            # 实时活动流
│   │   └── notificationStore.ts        # 通知/审批提醒
│   │
│   ├── services/                       # API请求封装
│   │   ├── api.ts                      # Axios实例 + 拦截器
│   │   ├── agentApi.ts
│   │   ├── taskApi.ts
│   │   ├── skillApi.ts
│   │   ├── gateApi.ts
│   │   ├── kpiApi.ts
│   │   └── auditApi.ts
│   │
│   ├── types/                          # TypeScript类型（与后端Schema对齐）
│   │   ├── agent.ts
│   │   ├── task.ts
│   │   ├── skill.ts
│   │   ├── gate.ts
│   │   ├── kpi.ts
│   │   └── websocket.ts
│   │
│   └── utils/
│       ├── constants.ts                # 角色/状态/颜色映射
│       └── formatters.ts              # 时间/数字/Token格式化
│
├── .env
├── tsconfig.json
├── vite.config.ts
└── package.json
```

### 3.2 路由设计

```typescript
// src/App.tsx

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import BasicLayout from './layouts/BasicLayout';

const routes = [
  { path: '/',                element: <Navigate to="/dashboard" /> },
  { path: '/dashboard',       element: <DashboardPage />,       name: '集群总览',   icon: 'DashboardOutlined' },
  { path: '/tasks',           element: <TaskListPage />,        name: '任务管线',   icon: 'ProjectOutlined' },
  { path: '/tasks/:id',       element: <TaskDetailPage /> },
  { path: '/gates',           element: <GatesPage />,           name: '审批中心',   icon: 'AuditOutlined' },
  { path: '/skills',          element: <SkillsPage />,          name: 'Skills管理', icon: 'BookOutlined' },
  { path: '/skills/:name',    element: <SkillDetailPage /> },
  { path: '/skills/new',      element: <SkillEditorPage /> },
  { path: '/kpi',             element: <KPIPage />,             name: 'KPI监控',   icon: 'LineChartOutlined' },
  { path: '/audit',           element: <AuditPage />,           name: '审计日志',   icon: 'FileSearchOutlined' },
  { path: '/config',          element: <ConfigPage />,          name: 'Agent配置', icon: 'SettingOutlined' },
  { path: '/circuit-breaker', element: <CircuitBreakerPage />,  name: '止损控制',   icon: 'ThunderboltOutlined' },
];
```

### 3.3 WebSocket Hook

```typescript
// src/hooks/useWebSocket.ts

import { useEffect, useRef, useCallback } from 'react';
import { useAgentStore } from '../stores/agentStore';
import { useActivityStore } from '../stores/activityStore';
import { useNotificationStore } from '../stores/notificationStore';

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws';
const RECONNECT_INTERVAL = 3000;
const HEARTBEAT_INTERVAL = 30000;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number>();
  const heartbeatTimer = useRef<number>();

  const updateAgent = useAgentStore((s) => s.updateAgent);
  const addActivity = useActivityStore((s) => s.addActivity);
  const addNotification = useNotificationStore((s) => s.addNotification);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[WS] Connected');
      // 心跳
      heartbeatTimer.current = window.setInterval(() => {
        ws.send(JSON.stringify({ type: 'ping' }));
      }, HEARTBEAT_INTERVAL);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleMessage(data);
    };

    ws.onclose = () => {
      console.log('[WS] Disconnected, reconnecting...');
      clearInterval(heartbeatTimer.current);
      reconnectTimer.current = window.setTimeout(connect, RECONNECT_INTERVAL);
    };

    ws.onerror = (err) => {
      console.error('[WS] Error:', err);
      ws.close();
    };
  }, []);

  const handleMessage = useCallback((data: any) => {
    switch (data.type) {
      case 'agent_status':
        // 更新Agent实时状态
        updateAgent(data.role, {
          status: data.status,
          model: data.model,
          totalTurns: data.total_turns,
          lastActive: data.timestamp,
        });
        break;

      case 'activity':
        // 添加到实时活动流
        addActivity({
          role: data.role,
          tool: data.tool,
          summary: data.args_summary,
          timestamp: data.timestamp,
        });
        break;

      case 'gate_created':
        // 新审批项 → 桌面通知 + 审批中心刷新
        addNotification({
          type: 'gate',
          title: `新审批: ${data.gate_type}`,
          message: `${data.task_title} 等待审批`,
          gateId: data.gate_id,
        });
        // 浏览器通知
        if (Notification.permission === 'granted') {
          new Notification('Agent审批提醒', {
            body: `${data.gate_type}: ${data.task_title}`,
          });
        }
        break;

      case 'gate_resolved':
        addActivity({
          role: 'system',
          tool: 'gate',
          summary: `${data.gate_type} ${data.action} by ${data.reviewer}`,
          timestamp: new Date().toISOString(),
        });
        break;

      case 'pong':
        break;

      default:
        console.log('[WS] Unknown event:', data.type);
    }
  }, [updateAgent, addActivity, addNotification]);

  useEffect(() => {
    connect();
    // 请求桌面通知权限
    if (Notification.permission === 'default') {
      Notification.requestPermission();
    }
    return () => {
      clearTimeout(reconnectTimer.current);
      clearInterval(heartbeatTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);
}
```

### 3.4 Zustand 状态管理

```typescript
// src/stores/agentStore.ts

import { create } from 'zustand';

interface AgentState {
  role: string;
  status: string;
  model?: string;
  skillsLoaded: string[];
  currentTaskId?: string;
  totalTurns?: number;
  lastActive?: string;
}

interface AgentStore {
  agents: Record<string, AgentState>;
  updateAgent: (role: string, partial: Partial<AgentState>) => void;
  setAgents: (agents: AgentState[]) => void;
}

const INITIAL_AGENTS: Record<string, AgentState> = {
  orchestrator: { role: 'orchestrator', status: 'stopped', skillsLoaded: [] },
  spec:         { role: 'spec',         status: 'stopped', skillsLoaded: [] },
  coding:       { role: 'coding',       status: 'stopped', skillsLoaded: [] },
  test:         { role: 'test',         status: 'stopped', skillsLoaded: [] },
  review:       { role: 'review',       status: 'stopped', skillsLoaded: [] },
  smoke:        { role: 'smoke',        status: 'stopped', skillsLoaded: [] },
  doc:          { role: 'doc',          status: 'stopped', skillsLoaded: [] },
};

export const useAgentStore = create<AgentStore>((set) => ({
  agents: INITIAL_AGENTS,

  updateAgent: (role, partial) =>
    set((state) => ({
      agents: {
        ...state.agents,
        [role]: { ...state.agents[role], ...partial },
      },
    })),

  setAgents: (agents) =>
    set({
      agents: Object.fromEntries(agents.map((a) => [a.role, a])),
    }),
}));
```

```typescript
// src/stores/activityStore.ts

import { create } from 'zustand';

interface Activity {
  id: string;
  role: string;
  tool: string;
  summary: string;
  timestamp: string;
}

interface ActivityStore {
  activities: Activity[];
  addActivity: (activity: Omit<Activity, 'id'>) => void;
}

const MAX_ACTIVITIES = 100;

export const useActivityStore = create<ActivityStore>((set) => ({
  activities: [],

  addActivity: (activity) =>
    set((state) => ({
      activities: [
        { ...activity, id: `${Date.now()}-${Math.random().toString(36).slice(2)}` },
        ...state.activities,
      ].slice(0, MAX_ACTIVITIES),
    })),
}));
```

### 3.5 API 请求封装

```typescript
// src/services/api.ts

import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api/v1',
  timeout: 30000,
});

// 请求拦截：JWT Token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截：统一错误处理
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      window.location.href = '/login';
    }
    return Promise.reject(err);
  }
);

export default api;
```

```typescript
// src/services/agentApi.ts

import api from './api';
import type { AgentStatusResponse, AgentConfigUpdate } from '../types/agent';

export const agentApi = {
  list:       ()                          => api.get<{ agents: AgentStatusResponse[] }>('/agents'),
  get:        (role: string)              => api.get<AgentStatusResponse>(`/agents/${role}`),
  updateConfig: (role: string, data: AgentConfigUpdate) => api.put(`/agents/${role}/config`, data),
  start:      (role: string)              => api.post(`/agents/${role}/start`),
  stop:       (role: string)              => api.post(`/agents/${role}/stop`),
  restart:    (role: string)              => api.post(`/agents/${role}/restart`),
  getSession: (role: string)              => api.get(`/agents/${role}/session`),
};
```

```typescript
// src/services/gateApi.ts

import api from './api';
import type { GateListResponse, GateDetailResponse } from '../types/gate';

export const gateApi = {
  list:    (status?: string)    => api.get<GateListResponse>('/gates', { params: { status } }),
  get:     (id: string)         => api.get<GateDetailResponse>(`/gates/${id}`),
  approve: (id: string, data: { reviewer: string; comment?: string }) =>
    api.post(`/gates/${id}/approve`, data),
  reject:  (id: string, data: { reviewer: string; reason: string }) =>
    api.post(`/gates/${id}/reject`, data),
  history: (days?: number)      => api.get<GateListResponse>('/gates/history', { params: { days } }),
};
```

### 3.6 React Query Hooks

```typescript
// src/hooks/useAgents.ts

import { useQuery } from '@tanstack/react-query';
import { agentApi } from '../services/agentApi';
import { useAgentStore } from '../stores/agentStore';
import { useEffect } from 'react';

export function useAgents() {
  const setAgents = useAgentStore((s) => s.setAgents);

  const query = useQuery({
    queryKey: ['agents'],
    queryFn: async () => {
      const res = await agentApi.list();
      return res.data.agents;
    },
    refetchInterval: 30000,  // 30秒轮询兜底（WebSocket主驱动）
  });

  // 初始化 → Zustand（后续由WebSocket实时更新）
  useEffect(() => {
    if (query.data) {
      setAgents(query.data);
    }
  }, [query.data, setAgents]);

  return query;
}
```

```typescript
// src/hooks/useGates.ts

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { gateApi } from '../services/gateApi';
import { message } from 'antd';

export function usePendingGates() {
  return useQuery({
    queryKey: ['gates', 'pending'],
    queryFn: async () => {
      const res = await gateApi.list('pending');
      return res.data;
    },
    refetchInterval: 10000,  // 审批页10秒轮询
  });
}

export function useApproveGate() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reviewer, comment }: {
      id: string; reviewer: string; comment?: string
    }) => gateApi.approve(id, { reviewer, comment }),

    onSuccess: () => {
      message.success('审批通过');
      queryClient.invalidateQueries({ queryKey: ['gates'] });
    },
    onError: () => {
      message.error('审批操作失败');
    },
  });
}

export function useRejectGate() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reviewer, reason }: {
      id: string; reviewer: string; reason: string
    }) => gateApi.reject(id, { reviewer, reason }),

    onSuccess: () => {
      message.warning('已打回');
      queryClient.invalidateQueries({ queryKey: ['gates'] });
    },
  });
}
```

### 3.7 核心页面组件

#### 集群总览页

```tsx
// src/pages/Dashboard/index.tsx

import { Row, Col, Card } from 'antd';
import { useAgents } from '../../hooks/useAgents';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useAgentStore } from '../../stores/agentStore';
import { useActivityStore } from '../../stores/activityStore';
import AgentCard from '../../components/AgentCard';
import ActivityFeed from '../../components/ActivityFeed';
import KPICard from '../../components/KPICard';
import PendingGates from '../../components/PendingGates';
import { useKPISummary } from '../../hooks/useKPI';

export default function DashboardPage() {
  // 建立WebSocket连接（全局）
  useWebSocket();
  // 初始加载Agent数据
  useAgents();

  const agents = useAgentStore((s) => s.agents);
  const activities = useActivityStore((s) => s.activities);
  const { data: kpi } = useKPISummary('day');

  const agentRoles = [
    'orchestrator', 'spec', 'coding', 'test', 'review', 'smoke', 'doc'
  ];

  return (
    <div style={{ padding: 24 }}>
      {/* Agent集群状态 */}
      <Card title="Agent集群状态" style={{ marginBottom: 24 }}>
        <Row gutter={[16, 16]}>
          {agentRoles.map((role) => (
            <Col key={role} xs={12} sm={8} md={6} lg={4} xl={3}>
              <AgentCard agent={agents[role]} />
            </Col>
          ))}
        </Row>
        <div style={{ marginTop: 12, color: '#888' }}>
          <StatusLegend agents={Object.values(agents)} />
        </div>
      </Card>

      <Row gutter={24}>
        {/* 实时活动流 */}
        <Col span={12}>
          <Card title="实时活动流" style={{ marginBottom: 24 }}>
            <ActivityFeed activities={activities.slice(0, 20)} />
          </Card>
        </Col>

        {/* 今日KPI速览 */}
        <Col span={12}>
          <Card title="今日KPI速览" style={{ marginBottom: 24 }}>
            {kpi && (
              <Row gutter={[12, 12]}>
                {kpi.metrics.slice(0, 6).map((m) => (
                  <Col key={m.name} span={8}>
                    <KPICard metric={m} />
                  </Col>
                ))}
              </Row>
            )}
          </Card>
        </Col>
      </Row>

      {/* 待处理审批 */}
      <PendingGates />
    </div>
  );
}
```

#### AgentCard 组件

```tsx
// src/components/AgentCard/index.tsx

import { Card, Tag, Tooltip } from 'antd';
import type { AgentState } from '../../stores/agentStore';

const STATUS_MAP: Record<string, { color: string; text: string }> = {
  running: { color: 'green',   text: '运行中' },
  idle:    { color: 'default', text: '空闲' },
  waiting: { color: 'orange',  text: '等待中' },
  error:   { color: 'red',     text: '故障' },
  stopped: { color: 'default', text: '已停止' },
};

const ROLE_NAMES: Record<string, string> = {
  orchestrator: '编排官',
  spec:         '方案官',
  coding:       '研发官',
  test:         '测试官',
  review:       '审计官',
  smoke:        '巡检官',
  doc:          '文档官',
};

interface Props {
  agent: AgentState;
}

export default function AgentCard({ agent }: Props) {
  const { color, text } = STATUS_MAP[agent.status] || STATUS_MAP.stopped;

  return (
    <Card
      size="small"
      hoverable
      style={{ textAlign: 'center' }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        {ROLE_NAMES[agent.role] || agent.role}
      </div>
      <Tag color={color}>{text}</Tag>
      {agent.currentTaskId && (
        <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
          Task: {agent.currentTaskId.slice(0, 8)}
        </div>
      )}
      {agent.model && (
        <Tooltip title={agent.model}>
          <div style={{ fontSize: 11, color: '#aaa', marginTop: 2 }}>
            {agent.model.replace('claude-', '').replace('-4', '')}
          </div>
        </Tooltip>
      )}
    </Card>
  );
}
```

#### 审批中心页

```tsx
// src/pages/Gates/index.tsx

import { Card, Tabs, Button, Input, Modal, List, Tag, Space } from 'antd';
import { CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { usePendingGates, useApproveGate, useRejectGate } from '../../hooks/useGates';

export default function GatesPage() {
  const { data: pending, isLoading } = usePendingGates();
  const approveMutation = useApproveGate();
  const rejectMutation = useRejectGate();
  const [rejectModal, setRejectModal] = useState<{ id: string } | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const GATE_TYPE_MAP: Record<string, { label: string; color: string }> = {
    spec_approval:   { label: 'Spec方案审批', color: 'blue' },
    review_confirm:  { label: 'Review确认',   color: 'orange' },
    final_signoff:   { label: '最终签收',      color: 'green' },
  };

  const handleApprove = (id: string) => {
    approveMutation.mutate({
      id,
      reviewer: 'current_user', // TODO: 从JWT解析
      comment: '',
    });
  };

  const handleReject = () => {
    if (!rejectModal) return;
    rejectMutation.mutate({
      id: rejectModal.id,
      reviewer: 'current_user',
      reason: rejectReason,
    });
    setRejectModal(null);
    setRejectReason('');
  };

  return (
    <div style={{ padding: 24 }}>
      <Tabs defaultActiveKey="pending" items={[
        {
          key: 'pending',
          label: `待审批 (${pending?.total || 0})`,
          children: (
            <List
              loading={isLoading}
              dataSource={pending?.items || []}
              renderItem={(gate) => {
                const typeInfo = GATE_TYPE_MAP[gate.gate_type] || { label: gate.gate_type, color: 'default' };
                return (
                  <Card style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div>
                        <Tag color={typeInfo.color}>{typeInfo.label}</Tag>
                        <span style={{ fontWeight: 600, marginLeft: 8 }}>{gate.task_title}</span>
                        <div style={{ color: '#888', marginTop: 8, fontSize: 13 }}>
                          等待 {Math.round(gate.waiting_minutes)} 分钟
                        </div>
                        {gate.content && (
                          <div style={{ marginTop: 12, background: '#fafafa', padding: 12, borderRadius: 4 }}>
                            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 13 }}>
                              {typeof gate.content === 'string'
                                ? gate.content.slice(0, 500)
                                : JSON.stringify(gate.content, null, 2).slice(0, 500)}
                            </pre>
                          </div>
                        )}
                      </div>
                      <Space>
                        <Button
                          type="primary"
                          icon={<CheckOutlined />}
                          onClick={() => handleApprove(gate.id)}
                          loading={approveMutation.isPending}
                        >
                          通过
                        </Button>
                        <Button
                          danger
                          icon={<CloseOutlined />}
                          onClick={() => setRejectModal({ id: gate.id })}
                        >
                          打回
                        </Button>
                      </Space>
                    </div>
                  </Card>
                );
              }}
            />
          ),
        },
        { key: 'history', label: '审批历史', children: <GateHistory /> },
      ]} />

      <Modal
        title="打回原因"
        open={!!rejectModal}
        onOk={handleReject}
        onCancel={() => setRejectModal(null)}
        okText="确认打回"
        okButtonProps={{ danger: true }}
      >
        <Input.TextArea
          rows={4}
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          placeholder="请输入打回原因，将反馈给Agent重新执行"
        />
      </Modal>
    </div>
  );
}
```

#### KPI Dashboard 页

```tsx
// src/pages/KPI/index.tsx

import { Row, Col, Card, Select } from 'antd';
import { useState } from 'react';
import KPICard from '../../components/KPICard';
import KPIChart from '../../components/KPIChart';
import CostAnalysis from '../../components/CostAnalysis';
import { useKPISummary, useKPITimeSeries } from '../../hooks/useKPI';

export default function KPIPage() {
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('week');
  const { data: summary } = useKPISummary(period);
  const { data: fprTrend } = useKPITimeSeries('fpr', 7);
  const { data: coverageTrend } = useKPITimeSeries('coverage', 7);

  return (
    <div style={{ padding: 24 }}>
      <Card
        title="KPI Dashboard"
        extra={
          <Select value={period} onChange={setPeriod} style={{ width: 120 }}>
            <Select.Option value="day">今日</Select.Option>
            <Select.Option value="week">本周</Select.Option>
            <Select.Option value="month">本月</Select.Option>
          </Select>
        }
      >
        {/* 核心指标卡片 */}
        {summary && (
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            {summary.metrics.map((m) => (
              <Col key={m.name} xs={12} sm={8} md={6}>
                <KPICard metric={m} />
              </Col>
            ))}
          </Row>
        )}

        {/* 趋势图 */}
        <Row gutter={24} style={{ marginBottom: 24 }}>
          <Col span={24}>
            <Card title="FPR + Coverage 趋势" size="small">
              <KPIChart
                series={[
                  { name: 'FPR', data: fprTrend?.points || [], color: '#1890ff' },
                  { name: 'Coverage', data: coverageTrend?.points || [], color: '#52c41a' },
                ]}
                warningLine={60}
              />
            </Card>
          </Col>
        </Row>

        {/* Token成本分析 */}
        <Row gutter={24}>
          <Col span={12}>
            <CostAnalysis summary={summary} />
          </Col>
          <Col span={12}>
            <Card title="Agent工作量分布" size="small">
              {/* ECharts饼图 */}
            </Card>
          </Col>
        </Row>
      </Card>
    </div>
  );
}
```

#### KPICard 组件

```tsx
// src/components/KPICard/index.tsx

import { Statistic, Tag } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, MinusOutlined } from '@ant-design/icons';
import type { KPIMetricValue } from '../../types/kpi';

const STATUS_MAP = {
  ok:       { color: 'green',  text: '达标' },
  warning:  { color: 'orange', text: '接近' },
  critical: { color: 'red',    text: '告警' },
};

const TREND_ICON = {
  up:   <ArrowUpOutlined style={{ color: '#52c41a' }} />,
  down: <ArrowDownOutlined style={{ color: '#ff4d4f' }} />,
  flat: <MinusOutlined style={{ color: '#999' }} />,
};

interface Props {
  metric: KPIMetricValue;
}

export default function KPICard({ metric }: Props) {
  const statusInfo = STATUS_MAP[metric.status] || STATUS_MAP.ok;

  const formatValue = (v: number, unit: string) => {
    if (unit === 'percent') return `${v}%`;
    if (unit === 'minutes') return `${v}min`;
    if (unit === 'currency') return `¥${v}`;
    return `${v}`;
  };

  return (
    <div style={{
      padding: 16,
      border: '1px solid #f0f0f0',
      borderRadius: 8,
      textAlign: 'center',
    }}>
      <Statistic
        title={metric.display_name}
        value={formatValue(metric.current_value, metric.unit)}
        suffix={TREND_ICON[metric.trend]}
      />
      <div style={{ marginTop: 8 }}>
        <Tag color={statusInfo.color}>{statusInfo.text}</Tag>
        <span style={{ fontSize: 11, color: '#aaa', marginLeft: 4 }}>
          目标: {formatValue(metric.target, metric.unit)}
        </span>
      </div>
    </div>
  );
}
```

#### KPIChart 组件（ECharts 趋势图）

```tsx
// src/components/KPIChart/index.tsx

import ReactECharts from 'echarts-for-react';
import type { KPITimeSeriesPoint } from '../../types/kpi';

interface Series {
  name: string;
  data: KPITimeSeriesPoint[];
  color: string;
}

interface Props {
  series: Series[];
  warningLine?: number;
}

export default function KPIChart({ series, warningLine }: Props) {
  const option = {
    tooltip: { trigger: 'axis' },
    legend: { data: series.map((s) => s.name) },
    xAxis: {
      type: 'time',
    },
    yAxis: {
      type: 'value',
      max: 100,
      axisLabel: { formatter: '{value}%' },
    },
    series: [
      ...series.map((s) => ({
        name: s.name,
        type: 'line',
        smooth: true,
        data: s.data.map((p) => [p.timestamp, p.value]),
        lineStyle: { color: s.color },
        itemStyle: { color: s.color },
      })),
      // 警戒线
      ...(warningLine !== undefined ? [{
        name: '警戒线',
        type: 'line',
        markLine: {
          silent: true,
          data: [{ yAxis: warningLine }],
          lineStyle: { color: '#ff4d4f', type: 'dashed' },
          label: { formatter: `警戒 ${warningLine}%` },
        },
        data: [],
      }] : []),
    ],
  };

  return <ReactECharts option={option} style={{ height: 300 }} />;
}
```

### 3.8 常量与工具函数

```typescript
// src/utils/constants.ts

export const AGENT_ROLES = [
  'orchestrator', 'spec', 'coding', 'test', 'review', 'smoke', 'doc'
] as const;

export const ROLE_DISPLAY: Record<string, { name: string; color: string }> = {
  orchestrator: { name: '编排官', color: '#722ed1' },
  spec:         { name: '方案官', color: '#1890ff' },
  coding:       { name: '研发官', color: '#52c41a' },
  test:         { name: '测试官', color: '#13c2c2' },
  review:       { name: '审计官', color: '#fa8c16' },
  smoke:        { name: '巡检官', color: '#eb2f96' },
  doc:          { name: '文档官', color: '#2f54eb' },
};

export const STAGE_ORDER = [
  'parse', 'spec', 'approve', 'code', 'test', 'review', 'smoke', 'doc', 'signoff'
];

export const KPI_DEFINITIONS: Record<string, {
  displayName: string; target: number; warning: number; unit: string;
}> = {
  fpr:              { displayName: 'Agent-FPR',      target: 80, warning: 60, unit: 'percent' },
  self_fix_rate:    { displayName: '自修复率',        target: 85, warning: 70, unit: 'percent' },
  plan_approval:    { displayName: '方案通过率',      target: 75, warning: 50, unit: 'percent' },
  coverage:         { displayName: '代码覆盖率',      target: 80, warning: 65, unit: 'percent' },
  contract_dev:     { displayName: '契约偏差率',      target: 0,  warning: 2,  unit: 'percent' },
  human_touch:      { displayName: 'Human-Touch',    target: 90, warning: 180, unit: 'minutes' },
  smoke_pass:       { displayName: 'Smoke通过率',    target: 90, warning: 75, unit: 'percent' },
  domain_score:     { displayName: '领域语义分',      target: 85, warning: 70, unit: 'percent' },
  skills_update:    { displayName: 'Skills更新频率', target: 5,  warning: 2,  unit: 'count' },
  token_vs_psp:     { displayName: 'Token/PSP比率', target: 15, warning: 30, unit: 'percent' },
  review_accuracy:  { displayName: 'Review准确率',   target: 90, warning: 75, unit: 'percent' },
  doc_satisfaction: { displayName: '文档满意度',      target: 80, warning: 60, unit: 'percent' },
};
```

---

## 四、前后端接口契约

### 4.1 WebSocket 事件协议

```typescript
// src/types/websocket.ts

// === 服务端 → 客户端事件 ===

interface AgentStatusEvent {
  type: 'agent_status';
  role: string;           // orchestrator/spec/coding/...
  status: string;         // running/idle/waiting/error
  model?: string;
  total_turns?: number;
  finish_reason?: string;
  timestamp: string;
}

interface ActivityEvent {
  type: 'activity';
  role: string;
  tool: string;           // execute/read/write/skill/...
  args_summary: string;   // 操作摘要
  timestamp: string;
}

interface ToolExecutingEvent {
  type: 'tool_executing';
  role: string;
  tool: string;
  timestamp: string;
}

interface GateCreatedEvent {
  type: 'gate_created';
  gate_id: string;
  gate_type: string;      // spec_approval/review_confirm/final_signoff
  task_id: string;
  task_title: string;
}

interface GateResolvedEvent {
  type: 'gate_resolved';
  gate_id: string;
  gate_type: string;
  action: 'approved' | 'rejected';
  reviewer: string;
}

type ServerEvent =
  | AgentStatusEvent
  | ActivityEvent
  | ToolExecutingEvent
  | GateCreatedEvent
  | GateResolvedEvent
  | { type: 'pong' };

// === 客户端 → 服务端事件 ===

interface PingEvent {
  type: 'ping';
}

type ClientEvent = PingEvent;
```

### 4.2 REST API 请求/响应对照表

| 接口 | 方法 | 请求体 | 响应体 | 状态码 |
|------|------|--------|--------|--------|
| `/agents` | GET | — | `{ agents: AgentStatusResponse[] }` | 200 |
| `/agents/{role}` | GET | — | `AgentStatusResponse` | 200 |
| `/agents/{role}/config` | PUT | `AgentConfigUpdate` | `{ status: "updated" }` | 200 |
| `/agents/{role}/start` | POST | — | `{ status: "started", role }` | 200 |
| `/agents/{role}/stop` | POST | — | `{ status: "stopped", role }` | 200 |
| `/tasks` | GET | `?status&page&size` | `TaskListResponse` | 200 |
| `/tasks` | POST | `TaskCreateRequest` | `TaskDetailResponse` | 201 |
| `/tasks/{id}` | GET | — | `TaskDetailResponse` | 200 |
| `/tasks/{id}/stages` | GET | — | `TaskStageResponse[]` | 200 |
| `/tasks/{id}/cancel` | POST | — | `{ status: "cancelled" }` | 200 |
| `/gates` | GET | `?status` | `GateListResponse` | 200 |
| `/gates/{id}` | GET | — | `GateDetailResponse` | 200 |
| `/gates/{id}/approve` | POST | `{ reviewer, comment? }` | `{ status: "approved" }` | 200 |
| `/gates/{id}/reject` | POST | `{ reviewer, reason }` | `{ status: "rejected" }` | 200 |
| `/gates/history` | GET | `?days` | `GateListResponse` | 200 |
| `/skills` | GET | `?layer&tag&role&status` | `SkillListResponse` | 200 |
| `/skills` | POST | `SkillCreateRequest` | `SkillDetailResponse` | 201 |
| `/skills/{name}` | GET | — | `SkillDetailResponse` | 200 |
| `/skills/{name}` | PUT | `SkillUpdateRequest` | `SkillDetailResponse` | 200 |
| `/skills/{name}` | DELETE | — | `{ status: "archived" }` | 200 |
| `/skills/{name}/versions` | GET | — | `SkillVersionListResponse` | 200 |
| `/skills/{name}/rollback` | POST | `?version` | `{ status: "rolled_back" }` | 200 |
| `/skills/stats` | GET | — | `SkillStatsResponse` | 200 |
| `/kpi/summary` | GET | `?period` | `KPISummaryResponse` | 200 |
| `/kpi/metrics/{name}` | GET | `?days` | `KPITimeSeriesResponse` | 200 |
| `/kpi/report` | GET | `?type` | `KPIReportResponse` | 200 |
| `/kpi/compare` | GET | `?months` | `PSPCompareResponse` | 200 |
| `/audit/logs` | GET | `?role&risk&start&end&page&size` | `AuditLogListResponse` | 200 |
| `/circuit-breaker/status` | GET | — | `CircuitBreakerStatus` | 200 |
| `/circuit-breaker/trigger` | POST | `{ level: 1\|2\|3, reason }` | `{ status: "triggered" }` | 200 |
| `/circuit-breaker/release` | POST | `{ reason }` | `{ status: "released" }` | 200 |

---

## 五、部署架构

### 5.1 Docker Compose（开发/测试环境）

```yaml
# docker-compose.yml

version: '3.8'

services:
  # 后端API
  api:
    build: ./agent-platform
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/agent_platform
      - REDIS_URL=redis://redis:6379/0
      - SKILLKIT_PATH=/opt/agent-skills-engine
    volumes:
      - ./skills:/skills                    # 三层Skills目录
      - ./agent-skills-engine:/opt/agent-skills-engine  # SkillKit代码
    depends_on:
      - db
      - redis

  # 前端SPA
  web:
    build: ./agent-platform-web
    ports:
      - "3000:80"
    depends_on:
      - api

  # PostgreSQL
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: agent_platform
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  # Redis
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  # Prometheus
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  # Grafana
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin

volumes:
  pgdata:
```

### 5.2 Nginx 配置

```nginx
# nginx.conf

upstream api_backend {
    server api:8000;
}

server {
    listen 80;
    server_name agent-platform.internal;

    # 前端SPA
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }

    # REST API 代理
    location /api/ {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket 代理
    location /ws {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }

    # Webhook 代理
    location /webhooks/ {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
    }
}
```

---

## 六、开发计划（Phase 3 细化）

| 周次 | 后端任务 | 前端任务 | 交付物 |
|------|---------|---------|--------|
| **W9** | FastAPI骨架 + DB模型 + Alembic迁移 | React项目初始化 + ProLayout + 路由 | 前后端空壳可运行 |
| **W9** | SkillKitBridge + EventCollector | WebSocket Hook + Zustand Store | 实时数据管道打通 |
| **W10** | Agent API + Task API | 集群总览页 + AgentCard + ActivityFeed | 看到Agent状态 |
| **W10** | Gate API + Gate Service | 审批中心页 + 通知推送 | 能审批 |
| **W11** | Skill API + Git同步 | Skills管理页 + Markdown编辑器 | 能管理Skills |
| **W11** | KPI API + Prometheus集成 | KPI Dashboard + ECharts图表 | 能看KPI |
| **W12** | Audit API + CircuitBreaker API | 审计日志页 + 止损控制台 | 全功能闭环 |
| **W12** | Jira/GitLab Webhook | 联调测试 + Bug修复 | 可交付MVP |

---

> **下一步**：基于本文档，可直接创建 `agent-platform/` 和 `agent-platform-web/` 项目骨架，开始Phase 3开发。
