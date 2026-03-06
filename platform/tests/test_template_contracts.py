from __future__ import annotations

from app.services.template_service import BUILTIN_TEMPLATES
from app.worker.graph import StageGraph
from app.worker.prompts import STAGE_INSTRUCTIONS


def _builtin_template(name: str) -> dict:
    return next(template for template in BUILTIN_TEMPLATES if template["name"] == name)


def test_quick_fix_builtin_contract_is_locked() -> None:
    quick_fix = _builtin_template("quick_fix")

    assert quick_fix["display_name"] == "快速修复"
    assert [stage["name"] for stage in quick_fix["stages"]] == [
        "parse",
        "code",
        "test",
        "signoff",
    ]
    assert [stage["agent_role"] for stage in quick_fix["stages"]] == [
        "orchestrator",
        "coding",
        "test",
        "orchestrator",
    ]
    assert [stage["order"] for stage in quick_fix["stages"]] == [0, 1, 2, 3]
    assert quick_fix["gates"] == []


def test_full_pipeline_builtin_contract_is_locked() -> None:
    full_pipeline = _builtin_template("full_pipeline")

    assert full_pipeline["display_name"] == "全流程"
    assert [stage["name"] for stage in full_pipeline["stages"]] == [
        "parse",
        "spec",
        "approve",
        "code",
        "test",
        "review",
        "smoke",
        "doc",
        "signoff",
    ]
    assert [stage["order"] for stage in full_pipeline["stages"]] == list(range(9))
    assert full_pipeline["gates"] == [
        {"after_stage": "spec", "type": "human_approve"},
        {"after_stage": "code", "type": "human_approve"},
        {"after_stage": "signoff", "type": "human_approve"},
    ]


def test_stage_instructions_cover_all_builtin_stage_names() -> None:
    builtin_stage_names = {
        stage["name"]
        for template in BUILTIN_TEMPLATES
        for stage in template["stages"]
    }

    assert builtin_stage_names.issubset(STAGE_INSTRUCTIONS)


def test_quick_fix_stage_graph_follows_builtin_order() -> None:
    quick_fix = _builtin_template("quick_fix")

    graph = StageGraph.from_template_stages(quick_fix["stages"])

    assert graph.nodes["parse"].depends_on == []
    assert graph.nodes["code"].depends_on == ["parse"]
    assert graph.nodes["test"].depends_on == ["code"]
    assert graph.nodes["signoff"].depends_on == ["test"]


def test_full_pipeline_stage_graph_follows_builtin_order() -> None:
    full_pipeline = _builtin_template("full_pipeline")

    graph = StageGraph.from_template_stages(full_pipeline["stages"])

    assert graph.nodes["parse"].depends_on == []
    assert graph.nodes["spec"].depends_on == ["parse"]
    assert graph.nodes["approve"].depends_on == ["spec"]
    assert graph.nodes["code"].depends_on == ["approve"]
    assert graph.nodes["test"].depends_on == ["code"]
    assert graph.nodes["review"].depends_on == ["test"]
    assert graph.nodes["smoke"].depends_on == ["review"]
    assert graph.nodes["doc"].depends_on == ["smoke"]
    assert graph.nodes["signoff"].depends_on == ["doc"]
