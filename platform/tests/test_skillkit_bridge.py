from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integration import skillkit_bridge as bridge_mod


@pytest.mark.asyncio
async def test_initialize_prefers_agentrunner_create(monkeypatch):
    captured: dict[str, object] = {}
    runner = SimpleNamespace(name="runner-from-create")

    class _FakeAgentRunner:
        @staticmethod
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return runner

    monkeypatch.setattr(bridge_mod, "SKILLKIT_AVAILABLE", True)
    monkeypatch.setattr(bridge_mod, "AgentRunner", _FakeAgentRunner)

    bridge = bridge_mod.SkillKitBridge()
    await bridge.initialize()

    assert bridge._runner is runner
    assert captured["kwargs"] == {"skill_dirs": []}


@pytest.mark.asyncio
async def test_initialize_supports_legacy_noarg_constructor(monkeypatch):
    class _FakeAgentRunner:
        def __init__(self):
            self.kind = "legacy-noarg"

    monkeypatch.setattr(bridge_mod, "SKILLKIT_AVAILABLE", True)
    monkeypatch.setattr(bridge_mod, "AgentRunner", _FakeAgentRunner)

    bridge = bridge_mod.SkillKitBridge()
    await bridge.initialize()

    assert bridge._runner.kind == "legacy-noarg"


@pytest.mark.asyncio
async def test_initialize_builds_engine_when_constructor_requires_engine(monkeypatch):
    class _FakeSkillsConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeSkillsEngine:
        def __init__(self, config=None):
            self.config = config

    class _FakeAgentRunner:
        def __init__(self, engine):
            self.engine = engine

    monkeypatch.setattr(bridge_mod, "SKILLKIT_AVAILABLE", True)
    monkeypatch.setattr(bridge_mod, "AgentRunner", _FakeAgentRunner)
    monkeypatch.setattr(bridge_mod, "SkillsConfig", _FakeSkillsConfig)
    monkeypatch.setattr(bridge_mod, "SkillsEngine", _FakeSkillsEngine)

    bridge = bridge_mod.SkillKitBridge()
    await bridge.initialize()

    assert isinstance(bridge._runner.engine, _FakeSkillsEngine)
    assert bridge._runner.engine.config.kwargs == {"skill_dirs": []}
