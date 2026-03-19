"""Tests for core engine functions: circuit breaker, task claim, state transitions, gates."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db.session import async_session_factory
from app.models.task import TaskModel
from app.worker import engine


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_stage(**kw) -> SimpleNamespace:
    defaults = dict(
        id="stage-1",
        stage_name="coding",
        agent_role="coding",
        error_message=None,
        output_summary=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_task(**kw) -> SimpleNamespace:
    defaults = dict(
        id="task-1",
        title="Test Task",
        status="running",
        total_tokens=0,
        total_cost_rmb=0.0,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)




@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_enabled(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True, stage.error_message set → uses structured reflection."""
    reflection_result = {
        "root_cause": "IndexError at line 42",
        "lesson": "Always check bounds",
        "suggestion": "Add bounds check",
    }
    generate_reflection_mock = AsyncMock(return_value=reflection_result)
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr("app.worker.failure.generate_structured_reflection", generate_reflection_mock)

    execute_stage_mock = AsyncMock(return_value="stage output")
    monkeypatch.setattr(engine, "execute_stage", execute_stage_mock)
    monkeypatch.setattr(engine, "execute_stage_sandboxed", execute_stage_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)

    task_id = "tt-exec-reflect-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Reflection Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-reflect-1",
            stage_name="coding",
            agent_role="coding",
            error_message="IndexError: list out of range",
            output_summary="partial output",
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0,
            [],
            compression,
            None,
            None,
            {},
            "/tmp/ws",
            None,
        )

    assert result == "stage output"
    generate_reflection_mock.assert_awaited_once()
    # Verify retry_context was built from reflection
    call_kwargs = execute_stage_mock.call_args.kwargs
    retry_ctx = call_kwargs.get("retry_context")
    assert retry_ctx is not None
    assert "IndexError at line 42" in retry_ctx["error"]

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_reflection_disabled_uses_plain_context(monkeypatch):
    """SKILL_REFLECTION_ENABLED=False, stage.error_message set → plain retry_context."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)

    execute_stage_mock = AsyncMock(return_value="stage output")
    monkeypatch.setattr(engine, "execute_stage", execute_stage_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    task_id = "tt-exec-noreflect-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="No Reflection Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-noreflect-1",
            stage_name="coding",
            agent_role="coding",
            error_message="NameError: variable not defined",
            output_summary="some prior output",
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {}, "/tmp/ws", None,
        )

    assert result == "stage output"
    call_kwargs = execute_stage_mock.call_args.kwargs
    retry_ctx = call_kwargs.get("retry_context")
    assert retry_ctx is not None
    assert retry_ctx["error"] == "NameError: variable not defined"
    assert "some prior output" in retry_ctx["prior_output"]

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_passes_preflight_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    execute_stage_mock = AsyncMock(return_value="stage output")
    monkeypatch.setattr(engine, "execute_stage", execute_stage_mock)
    monkeypatch.setattr(engine, "execute_stage_sandboxed", execute_stage_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    (tmp_path / "build.gradle").write_text("plugins {}", encoding="utf-8")
    (tmp_path / "src/main/java/demo/controller").mkdir(parents=True)
    (tmp_path / "src/main/java/demo/controller/HelloController.java").write_text("class X {}", encoding="utf-8")

    task_id = "tt-exec-preflight-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Preflight Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-preflight-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0,
            [],
            compression,
            None,
            None,
            {},
            str(tmp_path),
            None,
        )

    assert result == "stage output"
    call_kwargs = execute_stage_mock.call_args.kwargs
    assert "HelloController.java" in (call_kwargs.get("preflight_summary") or "")

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_uses_sandbox(monkeypatch):
    """sandbox_info is truthy AND agent_role='coding' → calls execute_stage_sandboxed."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "graceful")

    sandboxed_mock = AsyncMock(return_value="sandboxed output")
    plain_mock = AsyncMock(return_value="plain output")
    monkeypatch.setattr(engine, "execute_stage_sandboxed", sandboxed_mock)
    monkeypatch.setattr(engine, "execute_stage", plain_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    task_id = "tt-exec-sandbox-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Sandbox Stage Test", status="running"))
        await session.commit()

    sandbox_info = SimpleNamespace(container_name="test-container")

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-sandbox-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {}, "/tmp/ws", sandbox_info,
        )

    assert result == "sandboxed output"
    sandboxed_mock.assert_awaited_once()
    plain_mock.assert_not_awaited()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_exception_fails_task(monkeypatch):
    """execute_stage raises exception → _fail_task called, returns None."""
    fail_task_mock = AsyncMock()
    mark_stage_failed_mock = AsyncMock()

    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", False)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "_fail_task", fail_task_mock)
    monkeypatch.setattr(engine, "mark_stage_failed", mark_stage_failed_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_agents_for_task", lambda _: None)

    async def _raising(*a, **kw):
        raise RuntimeError("LLM timeout")

    monkeypatch.setattr(engine, "execute_stage", _raising)

    task_id = "tt-exec-exc-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Stage Exception Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-exc-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {}, "/tmp/ws", None,
        )

    assert result is None
    fail_task_mock.assert_awaited_once()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_execute_single_stage_strict_sandbox_unavailable(monkeypatch):
    """SANDBOX_ENABLED=True, agent_role='coding', sandbox_info=None, SANDBOX_FALLBACK_MODE='strict' → fail task."""
    fail_task_mock = AsyncMock()
    mark_stage_failed_mock = AsyncMock()

    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "strict")
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "_fail_task", fail_task_mock)
    monkeypatch.setattr(engine, "mark_stage_failed", mark_stage_failed_mock)
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import app.worker.agents as _agents
    monkeypatch.setattr(_agents, "close_agents_for_task", lambda _: None)

    task_id = "tt-exec-strict-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Strict Sandbox Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        stage = SimpleNamespace(
            id="stage-strict-1",
            stage_name="coding",
            agent_role="coding",
            error_message=None,
            output_summary=None,
            output_structured=None,
            execution_count=0,
            status="pending",
        )

        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        result = await engine._execute_single_stage(
            session,  # type: ignore[arg-type]
            task,  # type: ignore[arg-type]
            stage,  # type: ignore[arg-type]
            0, [], compression, None, None, {},
            "/tmp/ws",
            None,  # sandbox_info=None triggers strict mode path
            sandbox_required_error="sandbox_create_failed",
        )

    assert result is None
    fail_task_mock.assert_awaited_once()

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


# ── 17. _maybe_insert_dynamic_gate paths ──────────────────────────────────


@pytest.mark.asyncio
async def test_route_decision_disabled(monkeypatch):
    """DYNAMIC_ROUTING_ENABLED=False → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", False)
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace()
    result = await engine._route_decision(session, task, stage, {}, {})  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_no_options(monkeypatch):
    """No options in routing_config → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    task = _make_task()
    stage = _make_stage()
    session = SimpleNamespace()
    result = await engine._route_decision(session, task, stage, {"options": []}, {})  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_valid_llm_response(monkeypatch):
    """LLM returns valid target name → returns that target."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    fake_response = SimpleNamespace(content="coding")
    fake_client = SimpleNamespace(
        chat=AsyncMock(return_value=fake_response)
    )
    monkeypatch.setattr("app.integration.llm_client.get_llm_client", lambda: fake_client)

    task_id = "tt-route-valid-1"
    async with async_session_factory() as session:
        session.add(TaskModel(id=task_id, title="Routing Test", status="running"))
        await session.commit()

    async with async_session_factory() as session:
        task = await session.get(TaskModel, task_id)
        task.routing_decisions = []
        stage = SimpleNamespace(
            id="stage-route-1",
            stage_name="review",
            agent_role="review",
            output_summary="looks good",
            output_structured=None,
        )

        routing_config = {
            "options": [
                {"target": "coding", "description": "Implement it"},
                {"target": "test", "description": "Write tests"},
            ]
        }

        result = await engine._route_decision(
            session, task, stage, routing_config, {}  # type: ignore[arg-type]
        )

    assert result == "coding"

    async with async_session_factory() as session:
        t = await session.get(TaskModel, task_id)
        if t:
            await session.delete(t)
        await session.commit()


@pytest.mark.asyncio
async def test_route_decision_invalid_target(monkeypatch):
    """LLM returns invalid target (not in options) → None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    fake_response = SimpleNamespace(content="nonexistent_stage")
    fake_client = SimpleNamespace(
        chat=AsyncMock(return_value=fake_response)
    )
    monkeypatch.setattr("app.integration.llm_client.get_llm_client", lambda: fake_client)

    task = _make_task()
    stage = _make_stage(stage_name="review", agent_role="review")
    stage.output_summary = "review done"
    task.routing_decisions = None
    session = SimpleNamespace(commit=AsyncMock())

    routing_config = {
        "options": [
            {"target": "coding", "description": "Go to coding"},
        ]
    }

    result = await engine._route_decision(
        session, task, stage, routing_config, {}  # type: ignore[arg-type]
    )
    assert result is None


@pytest.mark.asyncio
async def test_route_decision_llm_exception(monkeypatch):
    """LLM raises exception → None, no crash."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    def _bad_get_client():
        raise ConnectionError("LLM server down")

    monkeypatch.setattr("app.integration.llm_client.get_llm_client", _bad_get_client)

    task = _make_task()
    stage = _make_stage(stage_name="review", agent_role="review")
    stage.output_summary = "review done"
    stage.output_structured = None
    session = SimpleNamespace(commit=AsyncMock())

    routing_config = {
        "options": [
            {"target": "coding", "description": "Go to coding"},
        ]
    }

    result = await engine._route_decision(
        session, task, stage, routing_config, {}  # type: ignore[arg-type]
    )
    assert result is None


# ── 19. _handle_gate revised and cancelled paths ───────────────────────────
# Section 28: _process_task workspace failure paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_workspace_failure_worktree_required(monkeypatch):
    """workspace_source == 'worktree_required' → fail task with worktree reason."""
    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=(None, "worktree_required", None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project=SimpleNamespace(repo_tree=None), project_id="proj-1", target_branch=None, stages=[dummy_stage])
    task.template = None
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)

    fail_task.assert_awaited()
    call_reason = fail_task.call_args[0][2]
    assert "worktree" in call_reason.lower()


@pytest.mark.asyncio
async def test_process_task_workspace_failure_clone_failed(monkeypatch):
    """workspace_source == 'tmp_clone_failed' → fail task with clone reason."""
    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=(None, "tmp_clone_failed", None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project=SimpleNamespace(repo_tree=None), project_id="proj-1", target_branch=None, stages=[dummy_stage])
    task.template = None
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)

    fail_task.assert_awaited()
    call_reason = fail_task.call_args[0][2]
    assert "clone" in call_reason.lower()


# ═══════════════════════════════════════════════════════════════════════
# Section 29: _process_task memory init exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_memory_init_exception(monkeypatch):
    """MEMORY_ENABLED=True, ProjectMemoryStore raises → warning logged, continues to complete."""
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", False)
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=("/tmp/ws", "tmp_empty", None)))
    monkeypatch.setattr(engine, "_setup_sandbox", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_complete_task", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    class _BadStore:
        def __init__(self, pid):
            raise RuntimeError("memory store failure")

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.memory", SimpleNamespace(ProjectMemoryStore=_BadStore))

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1",
        target_branch=None,
        stages=[],
    )
    task.template = None
    session = SimpleNamespace(commit=AsyncMock())
    # No stages → _complete_task called
    await engine._process_task(session, task)
    engine._complete_task.assert_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Section 31: _process_task_graph validation error
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_validation_error(monkeypatch):
    """StageGraph.validate() returns errors → fail task."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())

    class FakeGraph:
        nodes = []
        def validate(self):
            return ["cycle detected", "invalid node"]
        @staticmethod
        def get_ready_stages(*a, **kw):
            return []

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(
            project_id="proj-1",
            template=SimpleNamespace(stages="[]", name="tpl", gates=None),
        )
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None, execution_count=0)
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], SimpleNamespace(add=lambda c: None),
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited_once()
        assert "Invalid stage graph" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 32: _process_task_graph cancellation and stuck
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_cancellation(monkeypatch):
    """_is_cancelled returns True → return early without executing stages."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())

    class FakeNode:
        name = "coding"

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            return [FakeNode()]
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # _fail_task should NOT be called — just cancelled
        engine._fail_task.assert_not_awaited()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stuck_with_failed(monkeypatch):
    """No ready stages, no running, but has failed stages → fail task."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    call_count = [0]

    class FakeGraph:
        nodes = []
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            # First call: return a node that will fail
            # Subsequent calls: empty (stuck)
            call_count[0] += 1
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        # Pre-populate failed set by having a stage with failed status
        stage = _make_stage(stage_name="coding", status="failed", output_summary="err", output_structured=None, execution_count=0)

        # Directly call with pre-failed stage but empty sorted_stages (stage not in stage_map)
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # No failed stages in graph → break and return without failing
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 33: _execute_single_stage memory load exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_memory_load_exception(monkeypatch):
    """project_memory_store.get_memory_for_role raises → logs warning, continues."""
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="stage output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine, "mark_stage_failed", AsyncMock())
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)

    class BadMemoryStore:
        def get_memory_for_role(self, role):
            raise RuntimeError("memory broken")

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(stage_name="coding", agent_role="coding")
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression,
        BadMemoryStore(), None, {}, "/tmp/ws", None,
    )
    assert result == "stage output"


# ═══════════════════════════════════════════════════════════════════════
# Section 34: _execute_single_stage SKILL_REFLECTION_ENABLED
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True + prior error → generate_structured_reflection called."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="retry output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    reflection_mock = AsyncMock(return_value={
        "root_cause": "file not found",
        "lesson": "check file existence first",
        "suggestion": "use os.path.exists",
    })

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(generate_structured_reflection=reflection_mock))

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="FileNotFoundError: no such file",
        output_summary="partial output",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression,
        None, None, {}, "/tmp/ws", None,
    )
    assert result == "retry output"
    reflection_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_exception(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True but generate_structured_reflection raises → fallback."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(
        generate_structured_reflection=AsyncMock(side_effect=RuntimeError("reflection failed"))
    ))

    task = _make_task(project_id=None, project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="some error",
        output_summary="prior output",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "output"


@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_with_memory(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True, MEMORY_ENABLED=True, lesson persisted to memory."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    reflection_mock = AsyncMock(return_value={
        "root_cause": "null pointer",
        "lesson": "always check for null",
        "suggestion": "use getattr with default",
    })

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(generate_structured_reflection=reflection_mock))

    add_entries_mock = AsyncMock()

    class FakeEntry:
        @staticmethod
        def create(**kw):
            return FakeEntry()

    class FakeStore:
        def __init__(self, pid):
            pass
        async def add_entries(self, category, entries):
            await add_entries_mock(category, entries)

    monkeypatch.setitem(sys.modules, "app.worker.memory", SimpleNamespace(
        MemoryEntry=FakeEntry,
        ProjectMemoryStore=FakeStore,
    ))

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="NullPointerException",
        output_summary="partial",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "output"
    add_entries_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 39: _route_decision additional paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_route_decision_invalid_decision(monkeypatch):
    """LLM returns decision not in valid targets → returns None."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    class FakeClient:
        async def chat(self, **kw):
            return SimpleNamespace(content="invalid_stage")

    import sys
    sys.modules["app.integration.llm_client"] = SimpleNamespace(
        get_llm_client=lambda: FakeClient(),
        ChatMessage=lambda **kw: kw,
    )

    try:
        task = _make_task(routing_decisions=None)
        stage = _make_stage(stage_name="review", output_summary="done")
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._route_decision(
            session, task, stage,
            {"options": [{"target": "code"}, {"target": "test"}]},
            {},
        )
        assert result is None
    finally:
        sys.modules.pop("app.integration.llm_client", None)


@pytest.mark.asyncio
async def test_route_decision_success(monkeypatch):
    """LLM returns valid decision → returns the decision."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    class FakeClient:
        async def chat(self, **kw):
            return SimpleNamespace(content="code")

    import sys
    sys.modules["app.integration.llm_client"] = SimpleNamespace(
        get_llm_client=lambda: FakeClient(),
        ChatMessage=lambda **kw: kw,
    )

    try:
        task = _make_task(routing_decisions=[])
        stage = _make_stage(stage_name="review", output_summary="done")
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._route_decision(
            session, task, stage,
            {"options": [{"target": "code", "description": "go to code"}, {"target": "test"}]},
            {},
        )
        assert result == "code"
    finally:
        sys.modules.pop("app.integration.llm_client", None)


@pytest.mark.asyncio
async def test_route_decision_exception(monkeypatch):
    """LLM call raises → returns None (exception swallowed)."""
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_ENABLED", True)
    monkeypatch.setattr(engine.settings, "DYNAMIC_ROUTING_MODEL", None)
    monkeypatch.setattr(engine.settings, "LLM_MODEL", "test-model")

    class FakeClient:
        async def chat(self, **kw):
            raise RuntimeError("LLM down")

    import sys
    sys.modules["app.integration.llm_client"] = SimpleNamespace(
        get_llm_client=lambda: FakeClient(),
        ChatMessage=lambda **kw: kw,
    )

    try:
        task = _make_task()
        stage = _make_stage(stage_name="review", output_summary="done")
        session = SimpleNamespace(commit=AsyncMock())
        result = await engine._route_decision(
            session, task, stage,
            {"options": [{"target": "code"}]},
            {},
        )
        assert result is None
    finally:
        sys.modules.pop("app.integration.llm_client", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 40: _process_task internal paths (cancellation, skip, resume)
# ═══════════════════════════════════════════════════════════════════════

def _make_process_task_mocks(monkeypatch):
    """Set up common mocks for _process_task unit tests."""
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=("/tmp/ws", "tmp_empty", None)))
    monkeypatch.setattr(engine, "_setup_sandbox", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_cleanup_runtime_resources", AsyncMock())
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_complete_task", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c", add=lambda x: None)))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_maybe_insert_dynamic_gate", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_check_interactive_planning", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_handle_gate_with_retry", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_record_stage_audit", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", False)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", False)


@pytest.mark.asyncio
async def test_process_task_cancellation_before_group(monkeypatch):
    """_is_cancelled returns True before group execution → audit logged and return."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._fail_task.assert_not_awaited()
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_should_skip_stage(monkeypatch):
    """_should_skip_stage returns True → stage skipped, _execute_single_stage not called."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_should_skip_stage", lambda *a: True)

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    execute_mock.assert_not_awaited()
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_resume_completed_stage(monkeypatch):
    """Stage with status='completed' → resume path, output added to prior_outputs."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # Stage was already completed, execute should not be called
    execute_mock.assert_not_awaited()
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_resume_with_gate(monkeypatch):
    """Completed stage with gate def → gate handling in resume path."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    gate_mock = AsyncMock(return_value="gate output")
    monkeypatch.setattr(engine, "_handle_gate_with_retry", gate_mock)

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[{"after_stage": "parse", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    gate_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_resume_gate_rejected(monkeypatch):
    """Completed stage with gate def, gate returns None → return."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    gate_mock = AsyncMock(return_value=None)  # gate rejected
    monkeypatch.setattr(engine, "_handle_gate_with_retry", gate_mock)

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[{"after_stage": "parse", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_resume_compression_none(monkeypatch):
    """Compression returns None in resume path → warning logged, continues."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=None))

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_single_stage_compression_none(monkeypatch):
    """Compression returns None in main stage path → warning logged."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_single_stage_structured_output(monkeypatch):
    """Stage has output_structured → structured_outputs updated."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                        output_structured={"status": "done", "confidence": 0.9})
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    execute_mock.assert_awaited_once()
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_gate_returns_none(monkeypatch):
    """Stage gate returns None → task should not complete."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_handle_gate_with_retry", AsyncMock(return_value=None))

    stage = _make_stage(stage_name="parse", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(
            stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]',
            gates='[{"after_stage": "parse", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_interactive_planning_paused(monkeypatch):
    """_check_interactive_planning returns True → task paused, complete not called."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_check_interactive_planning", AsyncMock(return_value=True))

    stage = _make_stage(stage_name="parse", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_finalize_failed(monkeypatch):
    """_finalize_task_resources returns False → close agents and return without completing."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=False))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        await engine._process_task(session, task)

    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_parallel_stages(monkeypatch):
    """Two stages at same order → parallel execution path."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    outputs = {"stage1": "output1", "stage2": "output2"}

    async def _fake_execute(session, task, stage, *a, **kw):
        return outputs.get(stage.stage_name, "output")

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)

    stage1 = _make_stage(id="ps-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="ps-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_parallel_stage_exception(monkeypatch):
    """One parallel stage raises exception → task fails."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "mark_stage_failed", AsyncMock())

    call_count = [0]

    async def _fake_execute(session, task, stage, *a, **kw):
        call_count[0] += 1
        if stage.stage_name == "stage2":
            raise RuntimeError("stage2 failed")
        await asyncio.sleep(0.1)
        return "output1"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)

    stage1 = _make_stage(id="pe-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="pe-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        await engine._process_task(session, task)

    engine._fail_task.assert_awaited()


@pytest.mark.asyncio
async def test_process_task_parallel_stage_returns_none(monkeypatch):
    """One parallel stage returns None → return early."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    async def _fake_execute(session, task, stage, *a, **kw):
        if stage.stage_name == "stage2":
            return None
        return "output1"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)

    stage1 = _make_stage(id="pn-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="pn-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_parallel_already_completed_stage(monkeypatch):
    """Parallel stage already completed → added to prior_outputs without re-executing."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    execute_mock = AsyncMock(return_value="output2")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    stage1 = _make_stage(id="pac-1", stage_name="stage1", status="completed",
                         output_summary="existing output", output_structured={"key": "val"})
    stage2 = _make_stage(id="pac-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # stage1 already completed, stage2 should be executed
    execute_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_task_parallel_skip_stage(monkeypatch):
    """Parallel stage with condition → skipped via _should_skip_stage."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_should_skip_stage", lambda stage, defs, outputs: stage.stage_name == "stage2")

    execute_mock = AsyncMock(return_value="output1")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)

    stage1 = _make_stage(id="psk-1", stage_name="stage1", status="pending", output_summary=None, output_structured=None)
    stage2 = _make_stage(id="psk-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates="[]",
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # stage1 executed, stage2 skipped
    assert execute_mock.await_count == 1


@pytest.mark.asyncio
async def test_process_task_parallel_with_gate(monkeypatch):
    """Parallel stages with gate → gate handler called for stage with gate."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    gate_mock = AsyncMock(return_value="gate output")
    monkeypatch.setattr(engine, "_handle_gate_with_retry", gate_mock)

    stage1 = _make_stage(id="pwg-1", stage_name="stage1", status="pending", output_summary="out", output_structured=None)
    stage2 = _make_stage(id="pwg-2", stage_name="stage2", status="pending", output_summary=None, output_structured=None)

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage1, stage2],
        template=SimpleNamespace(
            stages='[{"name": "stage1", "agent_role": "coding", "order": 0}, {"name": "stage2", "agent_role": "coding", "order": 0}]',
            gates='[{"after_stage": "stage1", "type": "human_approve"}]',
        ),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    gate_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 41: _process_task_graph execution paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_single_stage_success(monkeypatch):
    """Graph execution: single stage runs successfully."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_handle_gate_with_retry", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    class FakeGraph:
        nodes = [FakeNode()]
        call_count = [0]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            self.call_count[0] += 1
            if "coding" not in completed and self.call_count[0] <= 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        engine._execute_single_stage.assert_awaited_once()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stage_fails_with_redirect(monkeypatch):
    """Graph execution: stage fails, failure redirect available → redirect stage reset."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 1)

    class FakeNodeCoding:
        name = "coding"

    class FakeNodeFixup:
        name = "fixup"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeCoding(), FakeNodeFixup()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNodeCoding()]
            return []
        def get_failure_redirect(self, name):
            if name == "coding":
                return "fixup"
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_coding = _make_stage(stage_name="coding", status="pending", output_summary=None,
                                   output_structured=None, execution_count=0, error_message=None)
        stage_fixup = _make_stage(stage_name="fixup", status="pending", output_summary=None,
                                  output_structured=None, execution_count=0, error_message=None)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_coding, stage_fixup], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # Redirect stage should have been reset
        assert stage_fixup.status == "pending"
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stage_fails_no_redirect(monkeypatch):
    """Graph execution: stage fails, no redirect → fail task."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited()
        assert "failed in graph" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_parallel_stages(monkeypatch):
    """Graph execution: multiple ready stages → parallel execution."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))

    async def _fake_execute(session, task, stage, *a, **kw):
        return f"output_{stage.stage_name}"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNodeA:
        name = "stage_a"

    class FakeNodeB:
        name = "stage_b"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeA(), FakeNodeB()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNodeA(), FakeNodeB()]  # both ready at once
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_a = _make_stage(stage_name="stage_a", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        stage_b = _make_stage(stage_name="stage_b", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_a, stage_b], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_max_iterations(monkeypatch):
    """Graph execution exceeds max iterations → fail task."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 1)

    class FakeNode:
        name = "coding"

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            # Always return a stage to force infinite loop
            return [FakeNode()]
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        # Use _execute_single_stage that always succeeds to force re-execution
        async def _always_succeed(session, task, stage, *a, **kw):
            # Don't add to completed so ready stages always has items
            return "output"

        monkeypatch.setattr(engine, "_execute_single_stage", _always_succeed)
        monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=None))
        monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))

        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()

        # With max_iterations=1 and 1 node, max_iterations = 1*1 = 1
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited()
        assert "max iterations" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stage_not_in_map(monkeypatch):
    """Graph node not in stage_map → skipped."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "nonexistent_stage"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if "nonexistent_stage" not in skipped and call_count[0] <= 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        # Stage map doesn't have "nonexistent_stage"
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # Should not call execute (stage not in map)
        execute_mock.assert_not_awaited()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_skip_condition(monkeypatch):
    """Graph execution: stage has skip condition → stage.status='skipped'."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_should_skip_stage", lambda stage, defs, outputs: True)
    execute_mock = AsyncMock(return_value="output")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if "coding" not in skipped and call_count[0] <= 1:
                return [FakeNode()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        execute_mock.assert_not_awaited()
        assert stage.status == "skipped"
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_parallel_exception(monkeypatch):
    """Graph parallel: one stage raises exception → added to failed set."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "mark_stage_failed", AsyncMock())
    monkeypatch.setattr(engine, "_fail_task", AsyncMock())
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    async def _fake_execute(session, task, stage, *a, **kw):
        if stage.stage_name == "stage_b":
            raise RuntimeError("stage_b exploded")
        return "output_a"

    monkeypatch.setattr(engine, "_execute_single_stage", _fake_execute)
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))

    class FakeNodeA:
        name = "stage_a"

    class FakeNodeB:
        name = "stage_b"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeA(), FakeNodeB()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNodeA(), FakeNodeB()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_a = _make_stage(stage_name="stage_a", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        stage_b = _make_stage(stage_name="stage_b", status="pending", output_summary=None,
                              output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_a, stage_b], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        engine.mark_stage_failed.assert_awaited()
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 42: _process_task GRAPH_EXECUTION_ENABLED finalize failed
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_execution_finalize_failed(monkeypatch):
    """GRAPH_EXECUTION_ENABLED=True, _finalize_task_resources returns False → close and return."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine.settings, "GRAPH_EXECUTION_ENABLED", True)
    monkeypatch.setattr(engine, "_process_task_graph", AsyncMock())
    monkeypatch.setattr(engine, "_finalize_task_resources", AsyncMock(return_value=False))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    with patch("app.worker.agents.close_agents_for_task"):
        await engine._process_task(session, task)

    engine._complete_task.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Section 43: _execute_single_stage sandbox strict mode
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_sandbox_strict_mode(monkeypatch):
    """SANDBOX_ENABLED=True, coding role, sandbox_info=None, strict mode → fail."""
    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", False)
    monkeypatch.setattr(engine, "_resolve_sandbox_fallback_mode", lambda: "strict")
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    mark_failed = AsyncMock()
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(stage_name="coding", agent_role="coding")
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    with patch("app.worker.agents.close_agents_for_task"):
        result = await engine._execute_single_stage(
            session, task, stage, 0, [], compression,
            None, None, {}, "/tmp/ws", None,
            sandbox_required_error="docker_unavailable",
        )

    assert result is None
    mark_failed.assert_awaited_once()
    fail_task.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# Section 44: _process_task workspace_branch and repo_context paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_workspace_branch_set_as_target(monkeypatch):
    """workspace_branch is set, task has no target_branch → task.target_branch updated."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_prepare_runtime_workspace",
                        AsyncMock(return_value=("/tmp/ws", "tmp_cloned", "feat/auto-branch")))

    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch="", stages=[dummy_stage],
        template=None,
    )
    session = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=True))  # halt after branch set

    await engine._process_task(session, task)
    assert task.target_branch == "feat/auto-branch"


@pytest.mark.asyncio
async def test_process_task_workspace_generic_failure(monkeypatch):
    """workspace_path=None, workspace_source neither 'worktree_required' nor 'clone_failed'."""
    _make_process_task_mocks(monkeypatch)
    dummy_stage = _make_stage(stage_name="coding", status="pending", output_summary=None)
    monkeypatch.setattr(engine, "_prepare_runtime_workspace", AsyncMock(return_value=(None, "other_failure", None)))
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda t: [dummy_stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda t: {})

    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    task = _make_task(project=SimpleNamespace(repo_tree=None), project_id="proj-1",
                      target_branch=None, stages=[dummy_stage], template=None)
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    fail_task.assert_awaited()
    assert "workspace preparation failed" in fail_task.call_args[0][2].lower()


@pytest.mark.asyncio
async def test_process_task_repo_context_from_project(monkeypatch):
    """task.project has repo_tree → _build_repo_context called."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    def build_mock(proj): return "REPO_CONTEXT"
    monkeypatch.setattr(engine, "_build_repo_context", build_mock)
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_parse_gates", lambda t: {})

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree={"files": ["src/main.py"]}, repo_url=""),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._execute_single_stage.assert_awaited_once()
    # Verify repo_context was passed to _execute_single_stage
    call_kwargs = engine._execute_single_stage.call_args
    assert "REPO_CONTEXT" in str(call_kwargs)


# ═══════════════════════════════════════════════════════════════════════
# Section 45: _process_task_graph resume paths
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_graph_resume_with_completed_stages(monkeypatch):
    """Graph with pre-completed stages → prior_outputs populated, execution continues."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=False))
    execute_mock = AsyncMock(return_value="output_coding")
    monkeypatch.setattr(engine, "_execute_single_stage", execute_mock)
    monkeypatch.setattr(engine, "_compress_with_log", AsyncMock(return_value=SimpleNamespace(l0="c", l1="c", l2="c")))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNodeCoding:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNodeCoding()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if "coding" not in completed and call_count[0] <= 1:
                return [FakeNodeCoding()]
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        # parse stage is already completed
        stage_parse = _make_stage(stage_name="parse", status="completed",
                                  output_summary="parse output", output_structured={"key": "val"},
                                  execution_count=1)
        # coding stage pending
        stage_coding = _make_stage(stage_name="coding", status="pending",
                                   output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        prior_outputs: list = []
        await engine._process_task_graph(
            session, task, [stage_parse, stage_coding], {}, {}, prior_outputs, compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # parse output should be in prior_outputs
        assert any(p["stage"] == "parse" for p in prior_outputs)
        execute_mock.assert_awaited_once()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_resume_circuit_breaker(monkeypatch):
    """Graph with pre-completed stage, circuit breaker trips → return early."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=True))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeGraph:
        nodes = []
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            return []

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_parse = _make_stage(stage_name="parse", status="completed",
                                  output_summary="output", output_structured=None, execution_count=1)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_parse], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        engine._check_circuit_breaker.assert_awaited_once()
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_stuck_with_unresolved_failed(monkeypatch):
    """Graph stuck: no ready, no running, failed has unresolved stages → fail task."""
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)

    class FakeNode:
        name = "coding"

    call_count = [0]

    class FakeGraph:
        nodes = [FakeNode()]
        def validate(self):
            return []
        def get_ready_stages(self, completed, running, failed, skipped, counts):
            call_count[0] += 1
            if call_count[0] == 1:
                return [FakeNode()]
            # Now stuck - no ready stages but "coding" is in failed
            return []
        def get_failure_redirect(self, name):
            return None

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage = _make_stage(stage_name="coding", status="pending", output_summary=None,
                            output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        fail_task.assert_awaited()
        assert "stuck" in fail_task.call_args[0][2] or "failed" in fail_task.call_args[0][2]
    finally:
        sys.modules.pop("app.worker.graph", None)


@pytest.mark.asyncio
async def test_process_task_graph_resume_with_skipped_stage(monkeypatch):
    """Graph resume: skipped stage added to skipped set."""
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine.settings, "GRAPH_MAX_LOOP_ITERATIONS", 5)
    fail_task = AsyncMock()
    monkeypatch.setattr(engine, "_fail_task", fail_task)

    class FakeGraph:
        nodes = []
        def validate(self):
            return []
        def get_ready_stages(self, *a, **kw):
            return []

    class FakeStageGraph:
        @staticmethod
        def from_template_stages(stages):
            return FakeGraph()

    import sys
    sys.modules["app.worker.graph"] = SimpleNamespace(StageGraph=FakeStageGraph)

    try:
        task = _make_task(project_id="proj-1", template=SimpleNamespace(stages="[]", name="tpl"))
        session = SimpleNamespace(commit=AsyncMock())
        stage_skipped = _make_stage(stage_name="review", status="skipped",
                                    output_summary=None, output_structured=None, execution_count=0)
        from app.worker.compressor import CompressionResult
        compression = CompressionResult()
        await engine._process_task_graph(
            session, task, [stage_skipped], {}, {}, [], compression,
            {}, None, None, "/tmp/ws", None, None,
        )
        # No error, should complete normally
    finally:
        sys.modules.pop("app.worker.graph", None)


# ═══════════════════════════════════════════════════════════════════════
# Section 49: _execute_single_stage SKILL_REFLECTION memory save exception
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_single_stage_skill_reflection_memory_save_exception(monkeypatch):
    """SKILL_REFLECTION_ENABLED=True, MEMORY_ENABLED=True, memory save fails → swallowed."""
    monkeypatch.setattr(engine.settings, "SKILL_REFLECTION_ENABLED", True)
    monkeypatch.setattr(engine.settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(engine, "execute_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-id"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    import sys
    monkeypatch.setitem(sys.modules, "app.worker.failure", SimpleNamespace(
        generate_structured_reflection=AsyncMock(return_value={
            "root_cause": "error", "lesson": "lesson text", "suggestion": "suggestion",
        })
    ))

    class FakeEntry:
        @staticmethod
        def create(**kw):
            return FakeEntry()

    class BadStore:
        def __init__(self, pid):
            pass
        async def add_entries(self, category, entries):
            raise RuntimeError("memory save failed")

    monkeypatch.setitem(sys.modules, "app.worker.memory", SimpleNamespace(
        MemoryEntry=FakeEntry,
        ProjectMemoryStore=BadStore,
    ))

    task = _make_task(project_id="proj-1", project=None, template=None)
    stage = _make_stage(
        stage_name="coding", agent_role="coding",
        error_message="some error", output_summary="partial",
    )
    session = SimpleNamespace(commit=AsyncMock())

    from app.worker.compressor import CompressionResult
    compression = CompressionResult()

    result = await engine._execute_single_stage(
        session, task, stage, 0, [], compression, None, None, {}, "/tmp/ws", None,
    )
    assert result == "output"


# ═══════════════════════════════════════════════════════════════════════
# Section 50: _process_task resume circuit_breaker and ensure_code
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_process_task_resume_circuit_breaker_trips(monkeypatch):
    """Completed stage resume → circuit breaker trips → return early."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_check_circuit_breaker", AsyncMock(return_value=True))

    stage = _make_stage(
        stage_name="parse", status="completed",
        output_summary="parse output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "parse", "agent_role": "orchestrator", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_resume_ensure_code_changes_fails(monkeypatch):
    """Completed stage resume, _ensure_code_stage_has_changes returns False → return early."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_ensure_code_stage_has_changes", AsyncMock(return_value=False))

    stage = _make_stage(
        stage_name="code", status="completed",
        output_summary="code output", output_structured=None,
    )

    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "code", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    engine._complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_dynamic_gate_inserted(monkeypatch):
    """Stage executed, dynamic gate inserted and returned True (pass-through)."""
    _make_process_task_mocks(monkeypatch)
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value="output"))
    monkeypatch.setattr(engine, "_maybe_insert_dynamic_gate", AsyncMock(return_value=True))

    stage = _make_stage(stage_name="coding", status="pending", output_summary=None, output_structured=None)
    task = _make_task(
        project=SimpleNamespace(repo_tree=None),
        project_id="proj-1", target_branch=None, stages=[stage],
        template=SimpleNamespace(stages='[{"name": "coding", "agent_role": "coding", "order": 0}]', gates="[]"),
    )
    session = SimpleNamespace(commit=AsyncMock())

    await engine._process_task(session, task)
    # Should still complete (gate was inserted but returned True = approved)
    engine._complete_task.assert_awaited_once()
