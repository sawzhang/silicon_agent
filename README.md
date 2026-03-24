# Silicon Agent

**Multi-agent platform that turns LLMs into a software engineering team.**

Define roles, wire a pipeline, add human gates — ship code with AI agents that spec, code, test, review, and document automatically.

[![CI](https://github.com/sawzhang/silicon_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/sawzhang/silicon_agent/actions/workflows/ci.yml)
[![E2E Smoke Test](https://github.com/sawzhang/silicon_agent/actions/workflows/e2e.yml/badge.svg)](https://github.com/sawzhang/silicon_agent/actions/workflows/e2e.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue)]()
[![License](https://img.shields.io/badge/license-proprietary-lightgrey)]()

## The Problem

Copilot autocompletes lines. ChatGPT answers questions. But neither can **own a task end-to-end** — from requirement to spec to code to test to review to deployment.

Real software delivery needs multiple specialists collaborating in a structured process with human oversight at critical points. Single-agent tools can't do this.

## The Solution

Silicon Agent is a **multi-agent orchestration platform** where each AI agent has a defined role in the software delivery lifecycle. They collaborate through a pipeline with built-in human approval gates, cost controls, and project memory.

```
Task In → Orchestrator → Spec Agent → Coding Agent → Test Agent → Review Agent → Smoke Agent → Doc Agent → Done
                              ↑              ↑              ↑
                          [Human Gate]   [Human Gate]   [Human Gate]
```

**7 specialized agents**, one pipeline, full human control.

## What You Get

### Multi-Agent Pipeline
- **7 roles**: orchestrator, spec, coding, test, review, smoke, doc — each with specialized skills and prompts
- **Automatic stage sequencing**: parse → spec → coding → test → review → smoke → doc
- **Checkpoint resume**: failed tasks restart from the failure point, completed stages are skipped
- **Per-role sandbox isolation**: each agent runs in its own sandbox with configurable resource limits

### Human-in-the-Loop
- **Approval gates** at critical stages — approve, reject, or redirect with comments
- **Real-time WebSocket** push for agent status, task progress, and gate notifications
- **Audit trail** for every agent action, LLM call, and human decision

### Production Safety
- **Circuit breaker**: auto-halt when token/cost limits are exceeded per task
- **KPI & ROI dashboard**: token usage, cost analysis, efficiency metrics, trend tracking
- **Project memory**: cross-task context accumulation — agents learn from previous tasks in the same project
- **LLM fallback**: tool calling failure auto-degrades to plain text mode

### Full-Stack Platform
- **Backend**: FastAPI + SQLAlchemy 2.0 (async) + WebSocket
- **Frontend**: React 18 + TypeScript + Ant Design + Zustand
- **12 dashboard pages**: Cockpit, Tasks, Gates, Skills, KPI, ROI, Audit, Config, and more
- **113 tests** passing (integration + unit)

## Architecture

```
silicon_agent/
├── platform/              # Backend — FastAPI + SQLAlchemy + AsyncIO
│   ├── app/
│   │   ├── api/v1/        # REST API (tasks, gates, skills, projects, kpi, audit...)
│   │   ├── worker/        # Agent Worker engine
│   │   │   ├── engine.py      # Pipeline orchestration
│   │   │   ├── executor.py    # Stage execution
│   │   │   ├── agents.py      # Role-based agent factory
│   │   │   ├── sandbox.py     # Per-role sandbox isolation
│   │   │   ├── memory.py      # Project memory system
│   │   │   └── gate_orchestrator.py  # Human approval flow
│   │   ├── models/        # SQLAlchemy ORM
│   │   ├── services/      # Business logic
│   │   └── websocket/     # Real-time event broadcast
│   └── tests/             # 113 tests
├── web/                   # Frontend — React + TypeScript + Ant Design
│   └── src/pages/         # Dashboard, Cockpit, Tasks, Gates, Skills, KPI, ROI...
├── skills/                # Agent skill definitions (per-role)
└── docs/design/           # Strategy whitepapers & architecture docs
```

## Quick Start

### Requirements

- Python 3.11+
- Node.js 18+
- SQLite (default) or PostgreSQL

### Backend

```bash
cd platform
cp .env.example .env
# Edit .env: set LLM_API_KEY, LLM_BASE_URL

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd web
npm install
npm run dev
# Open http://localhost:3000
```

### Tests

```bash
cd platform
pytest tests/ -v    # 113 tests
```

## API Endpoints

| Module | Path | Description |
|--------|------|-------------|
| Tasks | `/api/v1/tasks` | CRUD, batch create, cancel, retry |
| Gates | `/api/v1/gates` | Approval list, approve/reject |
| Skills | `/api/v1/skills` | Skill management, versioning, rollback |
| Projects | `/api/v1/projects` | Project management, repo analysis |
| Templates | `/api/v1/templates` | Pipeline template management |
| KPI | `/api/v1/kpi` | Metrics, trends, comparisons |
| Agents | `/api/v1/agents` | Agent status, configuration |
| Audit | `/api/v1/audit` | Audit log queries |
| Task Logs | `/api/v1/task-logs` | LLM & tool call log queries |
| Circuit Breaker | `/api/v1/circuit-breaker` | Cost control & halt management |

## Agent Roles

| Role | Stage | Responsibility |
|------|-------|---------------|
| **Orchestrator** | parse | Break down requirements into actionable tasks |
| **Spec** | spec | Write technical specifications and acceptance criteria |
| **Coding** | coding | Implement the solution according to spec |
| **Test** | test | Write and run tests, verify correctness |
| **Review** | review | Code review for quality, security, and best practices |
| **Smoke** | smoke | End-to-end smoke testing in staging environment |
| **Doc** | doc | Generate documentation and changelogs |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| Agent Engine | SkillKit AgentRunner |
| LLM | OpenAI-compatible API (MiniMax, GPT, Claude, etc.) |
| Frontend | React 18 + TypeScript |
| UI | Ant Design + ProComponents |
| State | Zustand + React Query |
| Real-time | WebSocket |
| CI | GitHub Actions |

## Roadmap

- [ ] Multi-repo support (monorepo and polyrepo project structures)
- [ ] Agent marketplace (plug-in custom roles and skills)
- [ ] GitOps integration (auto-create PRs, link to CI/CD)
- [ ] SaaS mode (multi-tenant, team management, billing)
- [ ] VS Code / IDE extension for in-editor agent interaction

## Docs

- [Architecture Overview](docs/architecture-overview.html)
- [SkillKit Integration](docs/skillkit-architecture.html)
- [QA & CI Architecture](docs/qa-ci-architecture.html)
- [Context Flow Diagram](docs/context-flow-diagram.html)

## License

Proprietary
