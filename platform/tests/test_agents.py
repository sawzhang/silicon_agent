"""Tests for the agent role tool whitelist configuration."""
from types import SimpleNamespace

import pytest

from app.worker import agents as agents_mod
from app.worker.agents import ROLE_TOOLS, _ALL_TOOLS


def test_role_tools_all_valid():
    """Every role's tool set should be a subset of all known tools."""
    for role, tools in ROLE_TOOLS.items():
        assert tools.issubset(_ALL_TOOLS), f"Role {role} has unknown tools: {tools - _ALL_TOOLS}"


def test_coding_has_core_tools():
    assert {"read", "write", "execute", "execute_script", "skill"}.issubset(ROLE_TOOLS["coding"])


def test_test_has_core_tools():
    assert {"read", "write", "execute", "execute_script", "skill"}.issubset(ROLE_TOOLS["test"])


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


def test_validate_role_tools_raises_on_unknown(monkeypatch):
    monkeypatch.setattr(agents_mod, "_ALL_TOOLS", {"read"})
    monkeypatch.setattr(agents_mod, "ROLE_TOOLS", {"coding": {"read", "write"}})
    with pytest.raises(RuntimeError):
        agents_mod.validate_role_tools_or_raise(fail_on_unknown=True)


def test_create_runner_does_not_pass_model_to_agentrunner(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeAgentRunner:
        @staticmethod
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                engine=object(),
                config=SimpleNamespace(model="env-default", load_context_files=False),
            )

    monkeypatch.setattr(agents_mod, "AgentRunner", _FakeAgentRunner)
    monkeypatch.setattr(agents_mod, "SKILLKIT_AVAILABLE", True)

    runner = agents_mod._create_runner(
        "doc",
        "tt-task-model-override",
        model="gpt-5.1-codex-mini",
        skill_dirs=[tmp_path],
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "model" not in kwargs
    assert runner.config.model == "gpt-5.1-codex-mini"


def test_create_runner_keeps_env_model_when_override_missing(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeAgentRunner:
        @staticmethod
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                engine=object(),
                config=SimpleNamespace(model="env-default", load_context_files=False),
            )

    monkeypatch.setattr(agents_mod, "AgentRunner", _FakeAgentRunner)
    monkeypatch.setattr(agents_mod, "SKILLKIT_AVAILABLE", True)

    runner = agents_mod._create_runner(
        "doc",
        "tt-task-model-default",
        model=None,
        skill_dirs=[tmp_path],
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "model" not in kwargs
    assert runner.config.model == "env-default"


def test_create_runner_applies_temperature_and_max_tokens_overrides(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeAgentRunner:
        @staticmethod
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                engine=object(),
                config=SimpleNamespace(
                    model="env-default",
                    temperature=1.0,
                    max_tokens=4096,
                    load_context_files=False,
                ),
            )

    monkeypatch.setattr(agents_mod, "AgentRunner", _FakeAgentRunner)
    monkeypatch.setattr(agents_mod, "SKILLKIT_AVAILABLE", True)

    runner = agents_mod._create_runner(
        "coding",
        "tt-task-config-override",
        temperature=0.3,
        max_tokens=8192,
        skill_dirs=[tmp_path],
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs
    assert runner.config.temperature == 0.3
    assert runner.config.max_tokens == 8192
