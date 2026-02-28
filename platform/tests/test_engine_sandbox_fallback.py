from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import engine
from app.worker.sandbox import SandboxCreateResult, SandboxManager


class _CompressionStub:
    def build_prior_context(self, _stage_index: int):
        return None


@pytest.mark.asyncio
async def test_sandbox_create_returns_workspace_not_found_when_host_path_missing(tmp_path):
    manager = SandboxManager()
    result = await manager.create(
        "task-missing-workspace",
        workspace=str(tmp_path / "missing"),
        workspace_source="fallback",
        image="sandbox-image:latest",
    )

    assert result.info is None
    assert result.error_code == "workspace_not_found"
    assert result.workspace_source == "fallback"


@pytest.mark.asyncio
async def test_execute_single_stage_strict_mode_fails_when_sandbox_unavailable(monkeypatch):
    session = SimpleNamespace()
    task = SimpleNamespace(id="task-strict-1")
    stage = SimpleNamespace(
        id="stage-strict-1",
        stage_name="code",
        agent_role="coding",
        error_message=None,
        output_summary=None,
    )

    mark_failed = AsyncMock()
    fail_task = AsyncMock()
    run_in_process = AsyncMock(return_value="should-not-run")
    run_sandbox = AsyncMock(return_value="should-not-run")

    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "strict")
    monkeypatch.setattr(engine, "mark_stage_failed", mark_failed)
    monkeypatch.setattr(engine, "_fail_task", fail_task)
    monkeypatch.setattr(engine, "execute_stage", run_in_process)
    monkeypatch.setattr(engine, "execute_stage_sandboxed", run_sandbox)
    monkeypatch.setattr("app.worker.agents.close_agents_for_task", lambda _task_id: None)

    result = await engine._execute_single_stage(
        session=session,  # type: ignore[arg-type]
        task=task,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        stage_index=0,
        prior_outputs=[],
        compression=_CompressionStub(),
        project_memory_store=None,
        repo_context=None,
        stage_defs={},
        workspace_path=None,
        sandbox_info=None,
        sandbox_required_error="docker_run_failed: boom",
    )

    assert result is None
    mark_failed.assert_awaited_once()
    fail_task.assert_awaited_once()
    run_in_process.assert_not_awaited()
    run_sandbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_task_emits_sandbox_fallback_events_on_create_failure(monkeypatch):
    stage = SimpleNamespace(
        id="stage-fallback-1",
        stage_name="parse",
        agent_role="orchestrator",
        status="pending",
        output_summary=None,
    )
    task = SimpleNamespace(
        id="task-fallback-1",
        title="fallback-test",
        status="pending",
        stages=[stage],
        template=None,
        project=None,
        project_id=None,
        total_tokens=0,
    )
    session = SimpleNamespace(commit=AsyncMock())

    fake_mgr = SimpleNamespace(
        create=AsyncMock(
            return_value=SandboxCreateResult(
                info=None,
                workspace="/tmp/sandbox/task-fallback-1",
                workspace_source="fallback",
                error_code="workspace_not_found",
                error_message="missing workspace",
            )
        ),
        destroy=AsyncMock(),
    )

    emitted: list[dict] = []

    async def _capture_emit(_task, **kwargs):
        emitted.append(kwargs)
        return f"log-{len(emitted)}"

    monkeypatch.setattr(engine.settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(engine.settings, "SANDBOX_FALLBACK_MODE", "graceful")
    monkeypatch.setattr(engine.settings, "WORKTREE_ENABLED", False)
    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda _task: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda _task: [stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda _task: {})
    monkeypatch.setattr(engine, "_group_stages_by_order", lambda _stages, _task: [[stage]])
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(side_effect=_capture_emit))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())
    monkeypatch.setattr("app.worker.sandbox.get_sandbox_manager", lambda: fake_mgr)

    await engine._process_task(session=session, task=task)  # type: ignore[arg-type]

    assert "sandbox_create_started" in [item["event_type"] for item in emitted]
    assert "sandbox_create_finished" in [item["event_type"] for item in emitted]
    assert "sandbox_fallback" in [item["event_type"] for item in emitted]


@pytest.mark.asyncio
async def test_process_task_always_cleans_up_sandbox_on_early_return(monkeypatch):
    stage = SimpleNamespace(
        id="stage-cleanup-1",
        stage_name="code",
        agent_role="coding",
        status="pending",
        output_summary=None,
        output_structured=None,
    )
    task = SimpleNamespace(
        id="task-cleanup-1",
        title="cleanup-test",
        status="pending",
        stages=[stage],
        template=None,
        project=None,
        project_id=None,
        total_tokens=0,
    )
    session = SimpleNamespace(commit=AsyncMock())
    fake_mgr = SimpleNamespace(destroy=AsyncMock())
    fake_info = SimpleNamespace(container_name="sa-sandbox-task-cleanup")

    monkeypatch.setattr(engine, "_safe_broadcast", AsyncMock())
    monkeypatch.setattr(engine.event_collector, "record_audit", AsyncMock())
    monkeypatch.setattr(engine, "_parse_gates", lambda _task: {})
    monkeypatch.setattr(engine, "_sort_stages", lambda _task: [stage])
    monkeypatch.setattr(engine, "_parse_stage_defs", lambda _task: {})
    monkeypatch.setattr(engine, "_group_stages_by_order", lambda _stages, _task: [[stage]])
    monkeypatch.setattr(engine, "_is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_setup_worktree", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(engine, "_setup_sandbox", AsyncMock(return_value=(fake_info, fake_mgr, None)))
    monkeypatch.setattr(engine, "_execute_single_stage", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_emit_system_log", AsyncMock(return_value="log-1"))
    monkeypatch.setattr(engine, "_close_started_system_log", AsyncMock())

    await engine._process_task(session=session, task=task)  # type: ignore[arg-type]

    fake_mgr.destroy.assert_awaited_once_with("task-cleanup-1")
