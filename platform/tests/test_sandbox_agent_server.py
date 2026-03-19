from __future__ import annotations

import importlib
import os
import sys
from types import ModuleType, SimpleNamespace


def _load_agent_server_with_fake_skillkit():
    class _FakeAgentRunner:
        def __init__(self, *args, **kwargs):
            self.engine = kwargs.get("engine")
            self.config = kwargs.get("config")

        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(
                engine=object(),
                config=SimpleNamespace(
                    model="env-default",
                    enable_reasoning=True,
                    temperature=1.0,
                    max_tokens=4096,
                    base_url="https://example.test/v1",
                ),
                get_tools=lambda: [],
            )

        def get_tools(self):
            return []

    fake_skillkit = ModuleType("skillkit")
    fake_skillkit.AgentRunner = _FakeAgentRunner
    sys.modules["skillkit"] = fake_skillkit
    fake_aiohttp = ModuleType("aiohttp")
    fake_aiohttp.web = SimpleNamespace(
        Request=object,
        Response=object,
        StreamResponse=object,
        Application=object,
        json_response=lambda *args, **kwargs: None,
        run_app=lambda *args, **kwargs: None,
    )
    sys.modules["aiohttp"] = fake_aiohttp
    sys.modules.pop("sandbox.agent_server", None)
    return importlib.import_module("sandbox.agent_server")


def test_create_runner_keeps_reasoning_for_gemini_model():
    agent_server = _load_agent_server_with_fake_skillkit()
    parsed = {
        "skill_dirs": [],
        "system_prompt": "system",
        "max_turns": 5,
        "enable_tools": True,
        "model": "gemini-2.5-pro",
        "temperature": None,
        "max_tokens": None,
        "workdir": "/workspace",
        "allowed_tools": {"read"},
    }
    runner = agent_server._create_runner(parsed)
    assert runner.config.enable_reasoning is True


def test_create_runner_keeps_reasoning_for_non_gemini_model():
    agent_server = _load_agent_server_with_fake_skillkit()
    parsed = {
        "skill_dirs": [],
        "system_prompt": "system",
        "max_turns": 5,
        "enable_tools": True,
        "model": "gpt-4o",
        "temperature": None,
        "max_tokens": None,
        "workdir": "/workspace",
        "allowed_tools": {"read"},
    }
    runner = agent_server._create_runner(parsed)
    assert runner.config.enable_reasoning is True


def test_sanitize_reasoning_kwargs_for_gemini_model():
    agent_server = _load_agent_server_with_fake_skillkit()
    kwargs = {"extra_body": {"reasoning_split": True, "foo": "bar"}, "x": 1}
    sanitized = agent_server._sanitize_reasoning_kwargs_for_model("gemini-2.5-pro", kwargs)
    assert "extra_body" in sanitized
    assert "reasoning_split" not in sanitized["extra_body"]
    assert sanitized["extra_body"]["foo"] == "bar"
    assert sanitized["x"] == 1


def test_sanitize_reasoning_kwargs_keeps_non_gemini_payload():
    agent_server = _load_agent_server_with_fake_skillkit()
    kwargs = {"extra_body": {"reasoning_split": True}, "x": 1}
    sanitized = agent_server._sanitize_reasoning_kwargs_for_model("gpt-4o", kwargs)
    assert sanitized == kwargs


def test_extract_gemini_thought_signatures_from_response():
    agent_server = _load_agent_server_with_fake_skillkit()

    class _Resp:
        def model_dump(self, mode=None):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "extra_content": {
                                        "google": {"thought_signature": "sig-1"}
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

    signatures = agent_server._extract_gemini_thought_signatures_from_response(_Resp())
    assert signatures == {"call-1": "sig-1"}


def test_inject_gemini_thought_signatures_into_messages():
    agent_server = _load_agent_server_with_fake_skillkit()
    kwargs = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "execute", "arguments": "{}"},
                    }
                ],
            }
        ]
    }
    injected = agent_server._inject_gemini_thought_signatures_into_messages(
        kwargs,
        {"call-1": "sig-1"},
    )
    tc = injected["messages"][0]["tool_calls"][0]
    assert tc["extra_content"]["google"]["thought_signature"] == "sig-1"


def test_detect_java_version_prefers_java8_markers(tmp_path):
    agent_server = _load_agent_server_with_fake_skillkit()
    pom = tmp_path / "pom.xml"
    pom.write_text("<properties><java.version>1.8</java.version></properties>", encoding="utf-8")
    assert agent_server._detect_java_major_version(str(tmp_path)) == 8


def test_detect_java_version_finds_java17_markers(tmp_path):
    agent_server = _load_agent_server_with_fake_skillkit()
    gradle = tmp_path / "build.gradle"
    gradle.write_text("sourceCompatibility = JavaVersion.VERSION_17", encoding="utf-8")
    assert agent_server._detect_java_major_version(str(tmp_path)) == 17


def test_detect_java_version_finds_gradle_toolchain_markers(tmp_path):
    agent_server = _load_agent_server_with_fake_skillkit()
    gradle = tmp_path / "build.gradle.kts"
    gradle.write_text(
        """
        java {
            toolchain {
                languageVersion.set(JavaLanguageVersion.of(17))
            }
        }
        """.strip(),
        encoding="utf-8",
    )
    assert agent_server._detect_java_major_version(str(tmp_path)) == 17


def test_detect_java_version_returns_none_without_markers(tmp_path):
    agent_server = _load_agent_server_with_fake_skillkit()
    settings = tmp_path / "settings.gradle"
    settings.write_text('rootProject.name = "demo"', encoding="utf-8")
    assert agent_server._detect_java_major_version(str(tmp_path)) is None


def test_configure_java_runtime_sets_java_home_and_path(tmp_path, monkeypatch):
    agent_server = _load_agent_server_with_fake_skillkit()
    gradle = tmp_path / "build.gradle"
    gradle.write_text("sourceCompatibility = 1.8", encoding="utf-8")

    monkeypatch.setenv("JAVA8_HOME", "/opt/jdk8")
    monkeypatch.setenv("JAVA17_HOME", "/opt/jdk17")
    monkeypatch.setenv("JAVA_HOME", "/opt/jdk17")
    monkeypatch.setenv("PATH", "/opt/jdk17/bin:/usr/bin:/bin")

    selected = agent_server._configure_java_runtime_for_workspace(str(tmp_path))
    assert selected == 8
    assert os.environ["JAVA_HOME"] == "/opt/jdk8"
    assert os.environ["PATH"].split(":")[0] == "/opt/jdk8/bin"


def test_configure_java_runtime_respects_explicit_override(tmp_path, monkeypatch):
    agent_server = _load_agent_server_with_fake_skillkit()
    monkeypatch.setenv("SANDBOX_JAVA_VERSION", "17")
    monkeypatch.setenv("JAVA17_HOME", "/opt/jdk17")
    monkeypatch.setenv("JAVA_HOME", "/opt/jdk8")
    monkeypatch.setenv("PATH", "/opt/jdk8/bin:/usr/bin:/bin")

    selected = agent_server._configure_java_runtime_for_workspace(str(tmp_path))
    assert selected == 17
    assert os.environ["JAVA_HOME"] == "/opt/jdk17"
    assert os.environ["PATH"].split(":")[0] == "/opt/jdk17/bin"


def test_configure_java_runtime_defaults_to_java8_without_markers(tmp_path, monkeypatch):
    agent_server = _load_agent_server_with_fake_skillkit()
    monkeypatch.delenv("SANDBOX_JAVA_VERSION", raising=False)
    monkeypatch.setenv("SANDBOX_DEFAULT_JAVA_VERSION", "8")
    monkeypatch.setenv("JAVA8_HOME", "/opt/jdk8")
    monkeypatch.setenv("JAVA_HOME", "/opt/jdk17")
    monkeypatch.setenv("PATH", "/opt/jdk17/bin:/usr/bin:/bin")

    selected = agent_server._configure_java_runtime_for_workspace(str(tmp_path))
    assert selected == 8
    assert os.environ["JAVA_HOME"] == "/opt/jdk8"
    assert os.environ["PATH"].split(":")[0] == "/opt/jdk8/bin"


def test_container_runner_keeps_gradlew_and_wraps_timeout(monkeypatch):
    agent_server = _load_agent_server_with_fake_skillkit()
    monkeypatch.setenv("SANDBOX_GRADLE_CMD_TIMEOUT_SECONDS", "480")
    runner = agent_server._create_runner(
        {
            "skill_dirs": [],
            "system_prompt": "system",
            "max_turns": 5,
            "enable_tools": True,
            "model": "gpt-4o",
            "temperature": None,
            "max_tokens": None,
            "workdir": "/workspace",
            "allowed_tools": {"execute"},
        }
    )
    normalized, args, err, result = runner._preprocess_validated_tool_call(
        tool_name="execute",
        args={"command": "cd /workspace && ./gradlew test"},
        tool_call={"name": "execute", "arguments": '{"command":"cd /workspace && ./gradlew test"}'},
    )
    assert err is None
    assert result is None
    assert "timeout 480s bash -lc" in args["command"]
    assert "./gradlew test" in args["command"]
    assert "gradle test" not in args["command"]
    assert normalized["arguments"]


def test_run_gradle_wrapper_prewarm_once_marks_done(tmp_path, monkeypatch):
    agent_server = _load_agent_server_with_fake_skillkit()
    gradlew = tmp_path / "gradlew"
    gradlew.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    gradlew.chmod(0o755)
    monkeypatch.setenv("SANDBOX_GRADLE_WRAPPER_PREWARM", "true")
    monkeypatch.setenv("SANDBOX_GRADLE_WRAPPER_PREWARM_TIMEOUT_SECONDS", "30")
    agent_server._WRAPPER_PREWARM_DONE = False
    import asyncio
    asyncio.run(agent_server._run_gradle_wrapper_prewarm_once(str(tmp_path)))
    assert agent_server._WRAPPER_PREWARM_DONE is True
