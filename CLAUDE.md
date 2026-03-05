# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

### Backend (platform/)
```bash
cd platform
pip install -e ".[dev]"                          # Install dependencies
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload  # Dev server
pytest tests/ -v                                 # Run all tests (471 tests)
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
| spec | read, write, edit, skill | Technical spec writing |
| coding | read, write, edit, execute, execute_script, skill | Code implementation |
| test | read, write, edit, execute, execute_script, skill | Test writing & execution |
| review | read, execute, skill | Code review |
| smoke | read, execute, skill | E2E smoke testing |
| doc | read, write, edit, skill | Documentation |

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

### Infrastructure

Tests use **file-based SQLite with WAL mode** (not in-memory) to eliminate reader-writer blocking when worker and test polling run concurrently. Key settings in `conftest.py`:

- `WORKER_ENABLED=False`, `JWT_ENABLED=False`, `SKILLKIT_ENABLED=False`
- `MEMORY_ENABLED=True` — but `_MEMORY_ROOT` is redirected to a `tempfile.mkdtemp()` dir so engine tests never write to `platform/memory/`
- `NullPool` — no connection reuse across tests
- WAL mode + `busy_timeout=30000` — concurrent reads/writes don't deadlock

```python
@pytest.mark.asyncio
async def test_example(client):  # client is httpx.AsyncClient against ASGI app
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 200
```

### Coverage Requirements

| Scope | Threshold | Current |
|-------|-----------|---------|
| Overall (`app/`) | **80%** (CI enforced via `--cov-fail-under=80`) | ~81% |
| `worker/engine.py` | **95%** | 95% |

Key services at 100%: `kpi_service`, `gate_service`, `task_service`, `template_service`,
`project_service`, `audit_service`, `circuit_breaker_service`, `task_log_service`, `prompts`.

Run with coverage locally:
```bash
cd platform
pytest tests/ -v --cov=app --cov-report=term-missing
```

CI posts a coverage comment on every PR showing per-file missing lines (green ≥ 80%, orange ≥ 60%).

### Unit Test Patterns for Worker/Engine

Engine internals cannot be tested through the HTTP API. Use `monkeypatch` + `SimpleNamespace` fakes:

**Mocking engine-level functions** — patch at the `engine` module level:

```python
from app.worker import engine
from unittest.mock import AsyncMock
from types import SimpleNamespace

@pytest.mark.asyncio
async def test_my_engine_path(monkeypatch):
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_complete_task", AsyncMock())

    task = SimpleNamespace(id="t-1", title="T", status="running",
                           project_id="p-1", project=None, template=None,
                           stages=[], target_branch=None, ...)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    await engine._process_task(session, task)
```

**Mocking `sys.modules` for lazily-imported modules** — use `monkeypatch.setitem` (NOT direct assignment), so pytest restores the original on teardown:

```python
import sys

monkeypatch.setitem(sys.modules, "app.worker.graph",
    SimpleNamespace(StageGraph=FakeStageGraph))
# No need for try/finally — monkeypatch handles cleanup
```

Direct `sys.modules["x"] = fake` + `sys.modules.pop("x")` in `finally` is **wrong**: `pop()` removes the entry entirely, so subsequent `patch("app.worker.x.attr")` calls reimport a fresh module object and patch the wrong copy.

**Mocking agent runner in executor tests**:

```python
fake_runner = SimpleNamespace(
    config=SimpleNamespace(model='test-model'),
    cumulative_usage=SimpleNamespace(total_tokens=100),
    default_cwd='/tmp/ws',
    reset_usage=lambda: None,
    chat=AsyncMock(return_value=SimpleNamespace(text_content='output')),
    events=SimpleNamespace(on=lambda *a, **kw: None),
)
monkeypatch.setattr(executor, 'get_agent', lambda *a, **kw: fake_runner)
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

## Development Tricks & Patterns

### Adding a New Model Field (Auto-Migration)

No Alembic needed. Add the column to the SQLAlchemy model with a default value — `init_db.py:_add_missing_columns()` auto-detects and runs `ALTER TABLE` + backfill on startup.

```python
# 1. Add to model (platform/app/models/task.py)
north_star: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

# 2. That's it. On next startup the column is added automatically.
# For NOT NULL columns, provide a server_default:
retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
```

Gotcha: `server_default` must be a string literal (SQL value), `default` is Python-level for backfill.

---

### Adding a New WebSocket Event (End-to-End)

**Step 1** — Define constant in `platform/app/websocket/events.py`:
```python
MY_NEW_EVENT = "my:new_event"
```

**Step 2** — Add mapping in `platform/app/websocket/manager.py` (`_EVENT_TYPE_MAP`):
```python
"my:new_event": "activity",   # or a new frontend type
```

**Step 3** — Broadcast from backend (use `_safe_broadcast` to swallow errors):
```python
from app.websocket.events import MY_NEW_EVENT
await _safe_broadcast(MY_NEW_EVENT, {"task_id": task.id, "detail": "..."})
```

**Step 4** — Handle in frontend `web/src/hooks/useWebSocket.ts` (`handleMessage` switch):
```typescript
case 'activity': {   // matches the mapped type
  addActivity(msg.payload as WSActivityPayload);
  break;
}
```

For a new frontend type, also add the case to the switch and a new Zustand/React Query action.

---

### Adding a New API Endpoint

Follow the existing three-layer pattern: route → service → dependency injection.

```python
# 1. Route (platform/app/api/v1/tasks.py)
@router.post("/{task_id}/north-star", response_model=TaskDetailResponse)
async def set_north_star(
    task_id: str,
    request: NorthStarRequest,
    service: TaskService = Depends(get_task_service),
):
    return await service.set_north_star(task_id, request)

# 2. Service method (platform/app/services/task_service.py)
async def set_north_star(self, task_id: str, request: NorthStarRequest) -> TaskDetailResponse:
    result = await self.session.execute(
        select(TaskModel).where(TaskModel.id == task_id).options(selectinload(TaskModel.stages))
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.north_star = request.north_star
    await self.session.commit()
    return self._task_to_response(task)
```

Always use `selectinload()` for relationships — lazy loading breaks with async sessions.

---

### Writing Tests (Fixtures & Mocking)

**Fixture pattern** — Use `tt-` prefix for seeded IDs, clean up in child-before-parent order (DB tests); use `gf-` prefix for gate-feedback tests:

```python
@pytest_asyncio.fixture
async def seed_my_data():
    async with async_session_factory() as session:
        obj = MyModel(id="tt-my-1", name="test")
        session.add(obj)
        await session.commit()
    yield "tt-my-1"
    async with async_session_factory() as session:
        result = await session.execute(select(MyModel).where(MyModel.id == "tt-my-1"))
        obj = result.scalar_one_or_none()
        if obj:
            await session.delete(obj)
        await session.commit()
```

**Mocking agent runner in executor tests** — monkeypatch module-level functions, use `SimpleNamespace` for lightweight fakes:

```python
@pytest.mark.asyncio
async def test_my_stage(monkeypatch):
    fake_runner = SimpleNamespace(
        config=SimpleNamespace(model='test-model'),
        cumulative_usage=SimpleNamespace(total_tokens=100),
        default_cwd='/tmp/ws',
        reset_usage=lambda: None,
        chat=AsyncMock(return_value=SimpleNamespace(text_content='output')),
        events=SimpleNamespace(on=lambda *a, **kw: None),
    )
    monkeypatch.setattr(executor, 'get_agent', lambda *a, **kw: fake_runner)
    monkeypatch.setattr(executor, '_safe_broadcast', AsyncMock())
    monkeypatch.setattr(executor, 'build_user_prompt', lambda _ctx: 'prompt')
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    # ... build task/stage SimpleNamespace objects and call execute_stage(...)
```

**Test isolation gotchas**:
- Never use `lambda` as a named assignment (`f = lambda x: x`) — use `def`. Ruff E731 enforces this.
- Never define two test functions with the same name in the same file. The second silently shadows the first and the first is never executed. Ruff F811 catches this.
- `session.refresh` in gate polling loops is called once at gate creation and again in the polling loop — mock carefully (allow first call, raise/return on subsequent ones).

---

### Frontend: React Query + Zustand Pattern

- **React Query** owns server data (tasks, templates, KPIs) — always invalidate via `queryClient.invalidateQueries`
- **Zustand** owns ephemeral real-time state (agent status, activity feed, notifications)
- **WebSocket** is the bridge: `task_update` events trigger React Query invalidation; `agent_status` events update Zustand directly

```typescript
// Fetch with React Query
const { data: tasks } = useQuery({
  queryKey: ['tasks', params],
  queryFn: () => listTasks(params),
});

// Mutate and invalidate
const createMutation = useMutation({
  mutationFn: createTask,
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tasks'] }),
});

// Read Zustand agent state (real-time, no server fetch needed)
const agents = useAgentStore((s) => s.agents);
```

Use `refetchInterval: 3000` on `useTask` only when `task.status === 'running'` — avoid polling otherwise.

---

### Development Session Checklist

Before starting any coding session on this project:

```bash
cd platform && pytest tests/ -v   # Establish baseline — all 471 tests must pass first
```

When implementing a new feature:
1. Write the test first (confirm it fails)
2. Implement to make the test pass
3. Run full suite to confirm no regressions
4. Check coverage for any modified modules: `pytest tests/ --cov=app/worker/engine --cov-report=term-missing`
5. If you discovered a new gotcha, add it to this section before closing the session

**Coverage targets to maintain**:
- `worker/engine.py` must stay ≥ 95%
- Overall `app/` must stay ≥ 80% (CI blocks merge if below)

**CI checks required before merge** (branch protection enforced):
- `backend-lint` — ruff check on `app/` and `tests/`
- `backend-test` — full pytest suite with `--cov-fail-under=80`
- `frontend-check` — TypeScript type check + build
