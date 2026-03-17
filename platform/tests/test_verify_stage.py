"""Tests for Phase 1.1: Verify Stage — role, prompts, tools, engine helpers, template."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.worker.prompts import STAGE_GUARDRAILS, STAGE_INSTRUCTIONS, SYSTEM_PROMPTS


# ── Role & prompts ──────────────────────────────────────────────


def test_verify_role_in_system_prompts():
    assert "verify" in SYSTEM_PROMPTS
    assert "验证" in SYSTEM_PROMPTS["verify"]


def test_verify_role_tools():
    from app.worker.agents import ROLE_TOOLS

    assert "verify" in ROLE_TOOLS
    assert ROLE_TOOLS["verify"] == {"execute", "read"}


def test_verify_stage_instruction_exists():
    assert "verify" in STAGE_INSTRUCTIONS
    assert "验证命令" in STAGE_INSTRUCTIONS["verify"]


def test_verify_guardrail_exists():
    assert "verify" in STAGE_GUARDRAILS
    assert "不要修改" in STAGE_GUARDRAILS["verify"]


# ── _resolve_verify_commands ────────────────────────────────────


def _make_task(project=None, template=None) -> SimpleNamespace:
    return SimpleNamespace(
        id="t-1", title="T", description="D",
        project_id="p-1", project=project,
        template=template, stages=[],
        target_branch=None, status="running",
    )


def test_resolve_verify_commands_from_stage_def():
    from app.worker.engine import _resolve_verify_commands

    task = _make_task()
    stage_defs = {"verify": {"verify_commands": ["npm run build"]}}
    assert _resolve_verify_commands(task, stage_defs) == ["npm run build"]


def test_resolve_verify_commands_from_project():
    from app.worker.engine import _resolve_verify_commands

    project = SimpleNamespace(
        verify_commands=json.dumps(["ruff check ."]),
        tech_stack=None,
    )
    task = _make_task(project=project)
    assert _resolve_verify_commands(task, {}) == ["ruff check ."]


def test_resolve_verify_commands_auto_detect_typescript():
    from app.worker.engine import _resolve_verify_commands

    project = SimpleNamespace(
        verify_commands=None,
        tech_stack=["typescript", "react"],
    )
    task = _make_task(project=project)
    cmds = _resolve_verify_commands(task, {})
    assert cmds is not None
    assert any("tsc" in c for c in cmds)


def test_resolve_verify_commands_auto_detect_python():
    from app.worker.engine import _resolve_verify_commands

    project = SimpleNamespace(
        verify_commands=None,
        tech_stack=["python", "fastapi"],
    )
    task = _make_task(project=project)
    cmds = _resolve_verify_commands(task, {})
    assert cmds is not None
    assert any("ruff" in c for c in cmds)


def test_resolve_verify_commands_returns_none_no_config():
    from app.worker.engine import _resolve_verify_commands

    task = _make_task()
    assert _resolve_verify_commands(task, {}) is None


# ── _template_needs_graph ───────────────────────────────────────


def _make_template(stages: list) -> SimpleNamespace:
    return SimpleNamespace(stages=stages)


def test_template_needs_graph_with_depends_on():
    from app.worker.engine import _template_needs_graph

    tpl = _make_template([{"name": "a", "depends_on": ["b"]}])
    task = _make_task(template=tpl)
    assert _template_needs_graph(task) is True


def test_template_needs_graph_with_on_failure():
    from app.worker.engine import _template_needs_graph

    tpl = _make_template([{"name": "a", "on_failure": "b"}])
    task = _make_task(template=tpl)
    assert _template_needs_graph(task) is True


def test_template_needs_graph_linear_returns_false():
    from app.worker.engine import _template_needs_graph

    tpl = _make_template([{"name": "a", "order": 0}, {"name": "b", "order": 1}])
    task = _make_task(template=tpl)
    assert _template_needs_graph(task) is False


def test_template_needs_graph_no_template():
    from app.worker.engine import _template_needs_graph

    task = _make_task(template=None)
    assert _template_needs_graph(task) is False


# ── harness_pipeline template structure ─────────────────────────


def test_harness_pipeline_template_structure():
    from app.services.template_service import BUILTIN_TEMPLATES

    harness = None
    for t in BUILTIN_TEMPLATES:
        if t["name"] == "harness_pipeline":
            harness = t
            break

    assert harness is not None, "harness_pipeline template not found"
    assert harness["display_name"] == "闭环流水线"

    stage_names = [s["name"] for s in harness["stages"]]
    assert "parse" in stage_names
    assert "spec" in stage_names
    assert "code" in stage_names
    assert "verify" in stage_names
    assert "test" in stage_names
    assert "review" in stage_names
    assert "signoff" in stage_names

    # Verify → code on_failure
    verify_stage = next(s for s in harness["stages"] if s["name"] == "verify")
    assert verify_stage["on_failure"] == "code"
    assert verify_stage["max_executions"] == 3

    # Test → code on_failure
    test_stage = next(s for s in harness["stages"] if s["name"] == "test")
    assert test_stage["on_failure"] == "code"

    # Code stage has max_executions
    code_stage = next(s for s in harness["stages"] if s["name"] == "code")
    assert code_stage["max_executions"] == 3

    # All stages have depends_on (graph mode)
    for s in harness["stages"]:
        assert "depends_on" in s, f"Stage {s['name']} missing depends_on"
