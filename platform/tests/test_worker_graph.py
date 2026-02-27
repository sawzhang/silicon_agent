from __future__ import annotations

from app.worker.graph import StageGraph


def test_from_template_stages_handles_none_and_invalid_json():
    assert StageGraph.from_template_stages(None).nodes == {}
    assert StageGraph.from_template_stages("not-json").nodes == {}
    assert StageGraph.from_template_stages("[]").nodes == {}


def test_from_template_stages_explicit_depends_on():
    stages = [
        {"name": "parse", "agent_role": "orchestrator"},
        {
            "name": "code",
            "agent_role": "coding",
            "depends_on": ["parse"],
            "condition": {"source_stage": "parse", "field": "ok", "operator": "eq", "value": True},
            "on_failure": "review",
            "max_executions": 2,
            "order": 2,
        },
        {"name": "review", "agent_role": "review"},
    ]

    graph = StageGraph.from_template_stages(stages)

    assert graph.nodes["parse"].depends_on == []
    assert graph.nodes["code"].depends_on == ["parse"]
    assert graph.nodes["code"].on_failure == "review"
    assert graph.nodes["code"].max_executions == 2


def test_from_template_stages_infers_linear_dependencies_from_order():
    stages = [
        {"name": "parse", "order": 1},
        {"name": "spec", "order": 1},
        {"name": "code", "order": 2},
        {"name": "test", "order": 3},
    ]

    graph = StageGraph.from_template_stages(stages)

    assert set(graph.nodes["parse"].depends_on) == set()
    assert set(graph.nodes["spec"].depends_on) == set()
    assert set(graph.nodes["code"].depends_on) == {"parse", "spec"}
    assert set(graph.nodes["test"].depends_on) == {"code"}


def test_get_ready_stages_and_retry_limits():
    stages = [
        {"name": "parse", "order": 1},
        {"name": "code", "order": 2, "max_executions": 2},
        {"name": "test", "order": 3},
    ]
    graph = StageGraph.from_template_stages(stages)

    ready0 = graph.get_ready_stages(completed=set(), running=set(), failed=set(), skipped=set())
    assert [s.name for s in ready0] == ["parse"]

    ready1 = graph.get_ready_stages(
        completed={"parse"},
        running=set(),
        failed={"code"},
        skipped=set(),
        execution_counts={"code": 1},
    )
    assert [s.name for s in ready1] == ["code"]

    ready2 = graph.get_ready_stages(
        completed={"parse"},
        running=set(),
        failed={"code"},
        skipped=set(),
        execution_counts={"code": 2},
    )
    assert ready2 == []


def test_get_failure_redirect_and_stage_names():
    graph = StageGraph.from_template_stages(
        [
            {"name": "a", "order": 1},
            {"name": "b", "order": 2, "on_failure": "a"},
            {"name": "c", "order": 3},
        ]
    )

    assert graph.get_failure_redirect("b") == "a"
    assert graph.get_failure_redirect("missing") is None

    names = graph.get_all_stage_names()
    assert set(names) == {"a", "b", "c"}
    assert names.index("a") < names.index("b")
    assert names.index("b") < names.index("c")


def test_validate_reports_unknown_dependencies_and_cycles():
    graph = StageGraph.from_template_stages(
        [
            {"name": "a", "depends_on": ["missing"]},
            {"name": "b", "depends_on": ["c"]},
            {"name": "c", "depends_on": ["b"]},
        ]
    )

    errors = graph.validate()
    assert any("unknown stage 'missing'" in e for e in errors)
    assert any("Cycle detected" in e for e in errors)
