# Silicon Agent Platform

硅基数字员工集群管理平台 — 基于 LLM 驱动的多 Agent 协作软件研发系统。

## 架构概览

```
silicon_agent/
├── platform/          # 后端 — FastAPI + SQLAlchemy + AsyncIO
│   ├── app/
│   │   ├── api/v1/    # REST API (tasks, gates, skills, projects, kpi, audit...)
│   │   ├── worker/    # Agent Worker 引擎 (engine → executor → agents)
│   │   ├── models/    # SQLAlchemy ORM 模型
│   │   ├── services/  # 业务逻辑层
│   │   ├── schemas/   # Pydantic 请求/响应模型
│   │   ├── websocket/ # 实时事件广播
│   │   └── middleware/ # Auth, 错误处理, 请求日志
│   ├── skills/        # Agent 技能定义 (per-role)
│   ├── memory/        # 项目记忆存储
│   ├── tests/         # 113 个集成/单元测试
│   └── alembic/       # 数据库迁移
├── web/               # 前端 — React + TypeScript + Ant Design
│   └── src/
│       ├── pages/     # Dashboard, Cockpit, Tasks, Gates, Skills, KPI, ROI...
│       ├── hooks/     # React Query hooks
│       ├── services/  # API 调用层
│       └── stores/    # Zustand 状态管理
├── docs/              # 设计文档与分析
│   ├── design/        # 系统设计方案、战略白皮书
│   └── media/         # PDF/视频资料 (不纳入版本控制)
└── .github/workflows/ # CI 配置
```

## 核心功能

- **多角色 Agent 协作**：orchestrator / spec / coding / test / review / smoke / doc 七个角色按流水线协作
- **任务流水线引擎**：自动编排 parse → spec → coding → test → review → smoke → doc 阶段
- **断点续跑**：任务失败后可从失败阶段重试，已完成阶段自动跳过
- **人工审批门控 (Gate)**：关键阶段插入 human-in-the-loop 审批，支持 approve/reject
- **LLM 兼容性**：支持 OpenAI / MiniMax / 其他兼容 API，tool calling 失败自动降级纯文本模式
- **实时状态推送**：WebSocket 推送 Agent 状态、任务进度、Gate 通知
- **KPI / ROI 监控**：Token 用量、成本分析、效率指标
- **止损熔断**：单任务 Token/成本超限自动触发 Circuit Breaker
- **项目记忆**：跨任务积累项目上下文，Agent 自动加载相关记忆
- **Skills 管理**：技能版本控制、回滚、按层级分类

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- SQLite (默认) 或 PostgreSQL

### 后端启动

```bash
cd platform
cp .env.example .env
# 编辑 .env 配置 LLM_API_KEY 和 LLM_BASE_URL

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 前端启动

```bash
cd web
npm install
npm run dev
# 访问 http://localhost:3000
```

### 运行测试

```bash
cd platform
source .venv/bin/activate
pytest tests/ -v
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| 数据库 | SQLite (dev) / PostgreSQL (prod) |
| Agent 引擎 | SkillKit AgentRunner |
| LLM | OpenAI 兼容 API (MiniMax M2.5 等) |
| 前端框架 | React 18 + TypeScript |
| UI 组件 | Ant Design + ProComponents |
| 状态管理 | Zustand + React Query |
| 实时通信 | WebSocket |
| CI | GitHub Actions |

## API 端点

| 模块 | 路径 | 说明 |
|------|------|------|
| Tasks | `/api/v1/tasks` | 任务 CRUD、批量创建、取消、重试 |
| Gates | `/api/v1/gates` | 审批门控列表、approve/reject |
| Skills | `/api/v1/skills` | 技能管理、版本、回滚 |
| Projects | `/api/v1/projects` | 项目管理、仓库分析 |
| Templates | `/api/v1/templates` | 流水线模板管理 |
| KPI | `/api/v1/kpi` | 指标汇总、趋势、对比 |
| Agents | `/api/v1/agents` | Agent 状态、配置 |
| Audit | `/api/v1/audit` | 审计日志查询 |
| Circuit Breaker | `/api/v1/circuit-breaker` | 熔断状态管理 |

## License

Proprietary - SITC Internal Use
