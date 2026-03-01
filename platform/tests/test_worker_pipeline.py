"""Worker pipeline integration tests.

Uses a real in-memory SQLite DB with a fake execute_stage that writes proper
DB fields without any LLM call.  This exercises the full _process_task
orchestration path that unit-level tests cannot reach:

  pending → claimed → running → [stage loop] → gate wait → completed / failed

Key design choices
------------------
* ``worker_pipeline`` fixture starts the real worker loop, patches only the
  two LLM-calling leaves (``execute_stage`` / ``execute_stage_sandboxed``).
  Everything else — DB writes, state-machine transitions, broadcasts, the
  gate polling loop, the circuit-breaker check — runs unmodified.
* ``MEMORY_COMPRESSION_ENABLED=False`` is already set in conftest, so
  ``compress_stage_output`` uses local fallback truncation (no LLM needed).
* All WebSocket broadcasts are captured in a list for assertion.
* ``_wait_until`` yields to the event loop so the worker asyncio task can run.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.gate import HumanGateModel
from app.models.task import TaskModel, TaskStageModel
from app.models.template import TaskTemplateModel
from app.websocket.events import CB_TRIGGERED, GATE_CREATED, TASK_STATUS_CHANGED
from app.worker import engine


# ── Fake execute_stage implementations ───────────────────────────────────────
# These replace the real executor so tests run without any LLM call.
# They must mirror exactly what executor._finalize_stage_success writes to DB,
# because engine.py reads stage.tokens_used and task.total_tokens after the call.

async def _fake_exec_ok(session, task, stage, prior_outputs, **kwargs) -> str:
    """Marks stage completed with 50 tokens — the standard fake for happy-path tests."""
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = 0.05
    stage.tokens_used = 50
    stage.output_summary = f"[fake] {stage.stage_name} output"
    await session.commit()

    task.total_tokens = (task.total_tokens or 0) + 50
    task.total_cost_rmb = (task.total_cost_rmb or 0.0) + 0.0001
    await session.commit()

    return f"[fake] {stage.stage_name} output"


async def _fake_exec_expensive(session, task, stage, prior_outputs, **kwargs) -> str:
    """Consumes 300 000 tokens — enough to trip the circuit breaker (limit = 200 000)."""
    stage.status = "completed"
    stage.completed_at = datetime.now(timezone.utc)
    stage.duration_seconds = 0.05
    stage.tokens_used = 300_000
    stage.output_summary = f"[expensive] {stage.stage_name} output"
    await session.commit()

    task.total_tokens = (task.total_tokens or 0) + 300_000
    task.total_cost_rmb = (task.total_cost_rmb or 0.0) + 300.0
    await session.commit()

    return f"[expensive] {stage.stage_name} output"


# ── Polling helpers ───────────────────────────────────────────────────────────

async def _wait_until(coro_fn, timeout: float = 8.0, interval: float = 0.05) -> bool:
    """Repeatedly await coro_fn() until truthy or timeout.

    Each sleep(interval) yields control to the event loop so the worker
    asyncio task can make progress.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await coro_fn():
            return True
        await asyncio.sleep(interval)
    return False


async def _task_has_status(task_id: str, status: str) -> bool:
    async with async_session_factory() as s:
        task = await s.get(TaskModel, task_id)
        return task is not None and task.status == status


async def _gate_is_pending(task_id: str) -> bool:
    async with async_session_factory() as s:
        result = await s.execute(
            select(HumanGateModel).where(
                HumanGateModel.task_id == task_id,
                HumanGateModel.status == "pending",
            )
        )
        return result.scalar_one_or_none() is not None


# ── worker_pipeline fixture ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def worker_pipeline(monkeypatch):
    """Start the real worker loop with LLM leaves patched out.

    What IS patched (external I/O only):
      - execute_stage / execute_stage_sandboxed → _fake_exec_ok
      - WebSocket broadcast → captured in ``broadcasts`` list
      - notify_* functions → AsyncMock (no HTTP calls)
      - event_collector.record_audit → AsyncMock (DB writes tested elsewhere)
      - Scheduler start/stop → AsyncMock (not under test here)
      - agents.close_agents_for_task / close_all_agents → no-op

    What is NOT patched (runs real code):
      - _pick_pending_task, _process_task, _complete_task, _fail_task
      - _handle_gate and its polling loop
      - _check_circuit_breaker
      - compress_stage_output (MEMORY_COMPRESSION_ENABLED=False → local fallback)
      - All DB writes and state transitions

    Yields: list[dict] — all (event, data) pairs broadcast during the test.
    """
    # Ensure worker is fully stopped from any previous test
    await engine.stop_worker()

    broadcasts: list[dict] = []

    async def _capture_broadcast(event: str, data: dict) -> None:
        broadcasts.append({"event": event, "data": data})

    # ── LLM leaves ────────────────────────────────────────────────────────────
    monkeypatch.setattr(engine, "execute_stage", _fake_exec_ok)
    monkeypatch.setattr(engine, "execute_stage_sandboxed", _fake_exec_ok)

    # ── External I/O ─────────────────────────────────────────────────────────
    monkeypatch.setattr(engine, "_safe_broadcast", _capture_broadcast)
    monkeypatch.setattr(engine, "notify_task_completed", AsyncMock())
    monkeypatch.setattr(engine, "notify_task_failed", AsyncMock())
    monkeypatch.setattr(engine, "notify_gate_created", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())

    # ── Agent pool cleanup (safe no-ops — no agents exist in tests) ───────────
    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_agents_for_task", lambda _: None)
    monkeypatch.setattr(_agents, "close_all_agents", lambda: None)

    # ── Scheduler ─────────────────────────────────────────────────────────────
    import app.worker.scheduler as _sched
    monkeypatch.setattr(_sched, "start_scheduler", AsyncMock())
    monkeypatch.setattr(_sched, "stop_scheduler", AsyncMock())

    # ── Settings that affect branching in _process_task ───────────────────────
    monkeypatch.setattr(engine.settings, "WORKER_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(engine.settings, "WORKER_GATE_MAX_WAIT_SECONDS", 10.0)
    monkeypatch.setattr(engine.settings, "WORKER_TASK_TIMEOUT", 30.0)
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "DYNAMIC_GATE_ENABLED", False)
    monkeypatch.setattr(engine.settings, "INTERACTIVE_PLANNING_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SKILL_FEEDBACK_ENABLED", False)
    # Circuit-breaker limits used by the CB test
    monkeypatch.setattr(engine.settings, "CB_MAX_TOKENS_PER_TASK", 200_000)
    monkeypatch.setattr(engine.settings, "CB_MAX_COST_PER_TASK_RMB", 500.0)

    await engine.start_worker()
    yield broadcasts
    await engine.stop_worker()


# ── Seed fixtures ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def linear_template():
    """parse → coding, no gates."""
    tmpl_id = "tt-wp-tmpl-linear"
    stages = json.dumps([
        {"name": "parse",  "agent_role": "orchestrator", "order": 0},
        {"name": "coding", "agent_role": "coding",       "order": 1},
    ])
    async with async_session_factory() as session:
        session.add(TaskTemplateModel(
            id=tmpl_id,
            name="tt_wp_linear",
            display_name="WP Linear",
            description="Two-stage linear template",
            stages=stages,
            gates="[]",
        ))
        await session.commit()

    yield tmpl_id

    async with async_session_factory() as session:
        for stage in (await session.execute(select(TaskStageModel))).scalars().all():
            if stage.task_id.startswith("tt-wp-"):
                await session.delete(stage)
        for task in (await session.execute(select(TaskModel))).scalars().all():
            if task.id.startswith("tt-wp-"):
                await session.delete(task)
        tmpl = await session.get(TaskTemplateModel, tmpl_id)
        if tmpl:
            await session.delete(tmpl)
        await session.commit()


@pytest_asyncio.fixture
async def gated_template():
    """parse → [human_approve gate] → coding."""
    tmpl_id = "tt-wp-tmpl-gated"
    stages = json.dumps([
        {"name": "parse",  "agent_role": "orchestrator", "order": 0},
        {"name": "coding", "agent_role": "coding",       "order": 1},
    ])
    gates = json.dumps([
        {"after_stage": "parse", "type": "human_approve", "max_retries": 0},
    ])
    async with async_session_factory() as session:
        session.add(TaskTemplateModel(
            id=tmpl_id,
            name="tt_wp_gated",
            display_name="WP Gated",
            description="Two-stage template with gate after parse",
            stages=stages,
            gates=gates,
        ))
        await session.commit()

    yield tmpl_id

    async with async_session_factory() as session:
        for gate in (await session.execute(select(HumanGateModel))).scalars().all():
            if gate.task_id.startswith("tt-wp-"):
                await session.delete(gate)
        for stage in (await session.execute(select(TaskStageModel))).scalars().all():
            if stage.task_id.startswith("tt-wp-"):
                await session.delete(stage)
        for task in (await session.execute(select(TaskModel))).scalars().all():
            if task.id.startswith("tt-wp-"):
                await session.delete(task)
        tmpl = await session.get(TaskTemplateModel, tmpl_id)
        if tmpl:
            await session.delete(tmpl)
        await session.commit()


def _make_task_with_stages(task_id: str, template_id: str, stage_names: list[str]):
    """Return (TaskModel, [TaskStageModel, ...]) — not yet added to a session."""
    task = TaskModel(
        id=task_id,
        title=f"Pipeline test — {task_id}",
        status="pending",
        template_id=template_id,
    )
    stages = [
        TaskStageModel(
            id=f"{task_id}-{name}",
            task_id=task_id,
            stage_name=name,
            agent_role="orchestrator" if name == "parse" else "coding",
            status="pending",
        )
        for name in stage_names
    ]
    return task, stages


# ── Test 1: Happy path ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_completes_all_stages(worker_pipeline, linear_template):
    """pending task with 2 stages runs to completion with correct DB state.

    This is the most important test: it exercises the complete _process_task
    orchestration (claimed → running → stage loop × 2 → _complete_task) for
    the first time, covering ~80 previously-untested lines in engine.py.
    """
    broadcasts = worker_pipeline
    task_id = "tt-wp-happy-1"

    async with async_session_factory() as session:
        task, stages = _make_task_with_stages(task_id, linear_template, ["parse", "coding"])
        session.add(task)
        session.add_all(stages)
        await session.commit()

    # Worker picks up the task automatically on its next poll (≤0.02 s).
    reached = await _wait_until(lambda: _task_has_status(task_id, "completed"))
    assert reached, "Task did not reach 'completed' within timeout"

    # ── DB assertions ─────────────────────────────────────────────────────────
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        assert task.status == "completed"
        assert task.completed_at is not None
        assert task.total_tokens == 100          # 50 tokens × 2 stages

        stage_map = {
            s.stage_name: s
            for s in (
                await session.execute(
                    select(TaskStageModel).where(TaskStageModel.task_id == task_id)
                )
            ).scalars().all()
        }

        parse = stage_map["parse"]
        assert parse.status == "completed"
        assert parse.tokens_used == 50
        assert parse.output_summary == "[fake] parse output"
        assert parse.completed_at is not None

        coding = stage_map["coding"]
        assert coding.status == "completed"
        assert coding.tokens_used == 50
        assert coding.output_summary == "[fake] coding output"

    # ── Broadcast assertions ──────────────────────────────────────────────────
    task_status_events = [
        b["data"]["status"]
        for b in broadcasts
        if b["event"] == TASK_STATUS_CHANGED and b["data"].get("task_id") == task_id
    ]
    assert "running" in task_status_events,   "Expected 'running' broadcast"
    assert "completed" in task_status_events, "Expected 'completed' broadcast"


# ── Test 2: Gate blocks then resumes on approval ──────────────────────────────

@pytest.mark.asyncio
async def test_gate_blocks_pipeline_then_resumes_on_approval(worker_pipeline, gated_template):
    """parse completes → gate created in DB → human approves → coding runs → done.

    Exercises _handle_gate's asyncio poll loop and the resume path.
    The test interleaves with the worker via real DB commits, not monkeypatching.
    """
    broadcasts = worker_pipeline
    task_id = "tt-wp-gate-1"

    async with async_session_factory() as session:
        task, stages = _make_task_with_stages(task_id, gated_template, ["parse", "coding"])
        session.add(task)
        session.add_all(stages)
        await session.commit()

    # ── Phase 1: wait for gate to appear (worker ran parse and is now blocked) ─
    gate_appeared = await _wait_until(lambda: _gate_is_pending(task_id))
    if not gate_appeared:
        async with async_session_factory() as _ds:
            _t = await _ds.get(TaskModel, task_id)
            _p = await _ds.get(TaskStageModel, f"{task_id}-parse")
            _tr = (await _ds.execute(
                select(TaskTemplateModel).where(TaskTemplateModel.id == gated_template)
            )).scalar_one_or_none()
            raise AssertionError(
                f"Gate was never created. "
                f"task.status={getattr(_t, 'status', 'MISSING')!r}, "
                f"task.template_id={getattr(_t, 'template_id', 'MISSING')!r}, "
                f"template_in_db={'YES' if _tr else 'NO'}, "
                f"parse.status={getattr(_p, 'status', 'MISSING')!r}"
            )

    # Task must be "running" (blocked inside _handle_gate), not yet completed.
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        assert task.status == "running", f"Expected 'running' at gate, got {task.status!r}"

        parse_stage = await session.get(TaskStageModel, f"{task_id}-parse")
        assert parse_stage is not None
        assert parse_stage.status == "completed"

        coding_stage = await session.get(TaskStageModel, f"{task_id}-coding")
        assert coding_stage is not None
        assert coding_stage.status == "pending", "Coding must not have started yet"

    # ── Phase 2: human approves the gate ──────────────────────────────────────
    # The worker is sleeping inside _handle_gate's poll loop. We commit the
    # approval into the shared in-memory SQLite; the worker's next
    # session.refresh(gate) will see it.
    async with async_session_factory() as session:
        result = await session.execute(
            select(HumanGateModel).where(
                HumanGateModel.task_id == task_id,
                HumanGateModel.status == "pending",
            )
        )
        gate = result.scalar_one()
        gate.status = "approved"
        gate.review_comment = "LGTM"
        await session.commit()

    # ── Phase 3: wait for full completion ─────────────────────────────────────
    reached = await _wait_until(lambda: _task_has_status(task_id, "completed"))
    assert reached, "Task did not complete after gate approval"

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task.status == "completed"
        assert task.total_tokens == 100  # parse (50) + coding (50)

        coding = await session.get(TaskStageModel, f"{task_id}-coding")
        assert coding is not None
        assert coding.status == "completed"

    # ── Broadcast assertions ──────────────────────────────────────────────────
    gate_created_events = [b for b in broadcasts if b["event"] == GATE_CREATED]
    assert len(gate_created_events) >= 1
    assert gate_created_events[0]["data"]["task_id"] == task_id
    assert gate_created_events[0]["data"]["gate_type"] == "human_approve"

    completed_events = [
        b for b in broadcasts
        if b["event"] == TASK_STATUS_CHANGED
        and b["data"].get("task_id") == task_id
        and b["data"].get("status") == "completed"
    ]
    assert len(completed_events) >= 1


# ── Test 3: Circuit breaker stops task mid-pipeline ───────────────────────────

@pytest.mark.asyncio
async def test_circuit_breaker_stops_task_after_first_stage(
    worker_pipeline, linear_template, monkeypatch
):
    """parse stage uses 300k tokens (> CB limit 200k) → task fails before coding runs.

    Exercises _check_circuit_breaker's trip path:
      _check_circuit_breaker → CircuitBreakerModel insert → CB_TRIGGERED broadcast
      → close_agents_for_task → _fail_task
    """
    broadcasts = worker_pipeline

    # Override execute_stage for this single test only.
    monkeypatch.setattr(engine, "execute_stage", _fake_exec_expensive)

    task_id = "tt-wp-cb-1"

    async with async_session_factory() as session:
        task, stages = _make_task_with_stages(task_id, linear_template, ["parse", "coding"])
        session.add(task)
        session.add_all(stages)
        await session.commit()

    reached = await _wait_until(lambda: _task_has_status(task_id, "failed"))
    assert reached, "Task did not reach 'failed' after circuit breaker trip"

    # ── DB assertions ─────────────────────────────────────────────────────────
    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.completed_at is not None

        # Coding stage must remain untouched — CB fired before it ran.
        coding = await session.get(TaskStageModel, f"{task_id}-coding")
        assert coding is not None
        assert coding.status == "pending", (
            f"Coding should still be pending after CB trip, got {coding.status!r}"
        )

        parse = await session.get(TaskStageModel, f"{task_id}-parse")
        assert parse is not None
        assert parse.status == "completed"   # parse itself completed; CB checked after
        assert parse.tokens_used == 300_000

    # ── Broadcast assertions ──────────────────────────────────────────────────
    cb_events = [b for b in broadcasts if b["event"] == CB_TRIGGERED]
    assert len(cb_events) >= 1, "Expected circuit_breaker:triggered broadcast"
    cb_data = cb_events[0]["data"]
    assert cb_data["task_id"] == task_id
    assert "tokens" in cb_data["reason"] or "Circuit breaker" in cb_data["reason"]

    failed_events = [
        b for b in broadcasts
        if b["event"] == TASK_STATUS_CHANGED
        and b["data"].get("task_id") == task_id
        and b["data"].get("status") == "failed"
    ]
    assert len(failed_events) >= 1, "Expected task:status_changed(failed) broadcast"
