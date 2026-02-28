# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

### Backend (platform/)
```bash
cd platform
pip install -e ".[dev]"                          # Install dependencies
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload  # Dev server
pytest tests/ -v                                 # Run all tests (113 tests)
pytest tests/test_tasks_api.py -v                # Run single test file
pytest tests/test_tasks_api.py::test_create_task -v  # Run single test
ruff check app/ tests/                           # Lint
```

### Frontend (web/)
```bash
cd web
npm install                     # Install dependencies
npm run dev                     # Dev server (port 3000, proxies /api → :8000)
npx tsc --noEmit                # Type check
npm run build                   # Production build
```

### Docker (full stack)
```bash
cd platform
docker-compose up               # Starts app + PostgreSQL + Redis
```

## Architecture

### Task Processing Pipeline

The system processes tasks through a multi-stage pipeline driven by a background worker:

1. **Worker Engine** (`worker/engine.py`) polls DB for pending tasks every 5s
2. Claims task atomically (pending → claimed → running) to prevent race conditions
3. For each stage in template order, calls **Executor** (`worker/executor.py`)
4. Executor creates/reuses a **SandboxedAgentRunner** (`worker/agents.py`) per (role, task_id)
5. AgentRunner calls LLM via SkillKit with role-specific system prompt and tool whitelist
6. Stage output feeds into next stage as prior context (with compression via `worker/compressor.py`)
7. Gates can pause execution between stages for human approval

**Resume from failure**: When a task is retried, the engine skips completed stages (rebuilds prior_outputs from `output_summary`) and re-executes from the failed stage. Failed stage error messages and prior output are injected as retry context (Ralph Loop V2 pattern) so the LLM can avoid repeating the same mistake.

**Tool-call fallback**: If LLM returns invalid tool JSON (e.g. MiniMax), executor auto-retries with `enable_tools=False`.

**Multi-model routing**: Each role/stage can use a different LLM model via `LLM_ROLE_MODEL_MAP` config or per-stage `model` field in template definitions.

**Parallel stage execution**: Stages with the same `order` value in a template execute concurrently.

**Git worktree isolation**: When `WORKTREE_ENABLED=true`, the worker prepares/uses a managed local repo cache and creates an isolated git worktree per task. On task completion, changes are committed, pushed, and a PR is created when repo config exists.

**External notifications**: When `NOTIFY_WEBHOOK_URL` is set, task lifecycle events (completed, failed, gate created) are POSTed to the webhook (compatible with Slack/飞书/钉钉).

### Agent Roles & Tools

| Role | Tools | Purpose |
|------|-------|---------|
| orchestrator | read, execute, skill | Task parsing & decomposition |
| spec | read, write, skill | Technical spec writing |
| coding | read, write, execute, execute_script, skill | Code implementation |
| test | read, write, execute, execute_script, skill | Test writing & execution |
| review | read, execute, skill | Code review |
| smoke | read, execute, skill | E2E smoke testing |
| doc | read, write, skill | Documentation |

### WebSocket Event Flow

Backend broadcasts events → `ws_manager` maps internal event names → frontend `useWebSocket` hook handles by type:
- `agent:status_changed` → `agent_status` → updates Zustand agent store
- `task:status_changed` / `task:stage_update` → `task_update` → invalidates React Query cache
- `gate:created` / `gate:resolved` → notifications

### Dependency Injection Pattern

Services receive AsyncSession via FastAPI Depends chain:
```python
# dependencies.py
async def get_task_service(session: AsyncSession = Depends(get_db)) -> TaskService:
    return TaskService(session)

# routes
@router.get("/tasks")
async def list_tasks(service: TaskService = Depends(get_task_service)):
    ...
```

### Database

- **Dev/Test**: SQLite (aiosqlite), in-memory for tests
- **Prod**: PostgreSQL (asyncpg)
- All relationships use `selectinload()` for async compatibility
- Auto-migration on startup adds missing columns via `ALTER TABLE` (no Alembic in dev)
- Key models: TaskModel → TaskStageModel (cascade), AgentModel (unique per role), HumanGateModel, TaskTemplateModel, ProjectModel

### Frontend State

- **Zustand**: Agent status, activity feed, notifications (real-time WebSocket updates)
- **React Query**: API data caching with WebSocket-triggered invalidation
- **UI**: Ant Design + ProComponents, ECharts for KPI charts

## Testing

Tests use in-memory SQLite with patched settings (`WORKER_ENABLED=False`, `JWT_ENABLED=False`). Fixtures in `conftest.py` create tables once per session and provide a fresh `AsyncClient` per test function.

```python
@pytest.mark.asyncio
async def test_example(client):  # client is httpx.AsyncClient against ASGI app
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 200
```

## Key Configuration (platform/app/config.py)

All settings load from `.env` via Pydantic BaseSettings. Critical ones:
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` — LLM provider config
- `LLM_ROLE_MODEL_MAP` — JSON mapping roles to specific models (e.g. `{"coding": "gpt-4o", "review": "claude-sonnet-4-20250514"}`)
- `WORKER_ENABLED` — enables background task processing
- `WORKER_STAGE_TIMEOUT` (300s) / `WORKER_TASK_TIMEOUT` (1800s) — execution limits
- `CB_MAX_TOKENS_PER_TASK` (200k) / `CB_MAX_COST_PER_TASK_RMB` (¥50) — circuit breaker
- `MEMORY_ENABLED` — project-level memory across tasks
- `WORKTREE_ENABLED` / `WORKTREE_BASE_DIR` / `WORKTREE_REPO_CACHE_DIR` — git worktree isolation and local repo cache
- `NOTIFY_WEBHOOK_URL` / `NOTIFY_EVENTS` — external webhook notifications

## Conventions

- Backend code is Python, all prompts/UI labels are in Chinese
- All httpx.AsyncClient instances must use `proxy=None` (system has SOCKS proxy env vars)
- Ruff lint: line length 100, target Python 3.11
- TypeScript strict mode enabled
- API base path: `/api/v1/`
