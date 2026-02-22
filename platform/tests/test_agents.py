"""Tests for the agent role tool whitelist configuration."""
from app.worker.agents import ROLE_TOOLS, _ALL_TOOLS


def test_role_tools_all_valid():
    """Every role's tool set should be a subset of all known tools."""
    for role, tools in ROLE_TOOLS.items():
        assert tools.issubset(_ALL_TOOLS), f"Role {role} has unknown tools: {tools - _ALL_TOOLS}"


def test_coding_has_all_tools():
    assert ROLE_TOOLS["coding"] == _ALL_TOOLS


def test_test_has_all_tools():
    assert ROLE_TOOLS["test"] == _ALL_TOOLS


def test_spec_no_execute():
    tools = ROLE_TOOLS["spec"]
    assert "execute" not in tools
    assert "execute_script" not in tools
    assert "read" in tools
    assert "write" in tools


def test_review_readonly():
    tools = ROLE_TOOLS["review"]
    assert "write" not in tools
    assert "execute_script" not in tools
    assert "read" in tools
    assert "execute" in tools


def test_doc_no_execute():
    tools = ROLE_TOOLS["doc"]
    assert "execute" not in tools
    assert "execute_script" not in tools
    assert "write" in tools


def test_orchestrator_no_write():
    tools = ROLE_TOOLS["orchestrator"]
    assert "write" not in tools
    assert "execute_script" not in tools
