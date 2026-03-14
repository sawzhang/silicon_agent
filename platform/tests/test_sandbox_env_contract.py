from app.worker.sandbox import DockerSandboxBackend
from pathlib import Path


def _extract_env_vars_from_docker_cmd(tokens: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for idx, token in enumerate(tokens):
        if token != "-e" or idx + 1 >= len(tokens):
            continue
        pair = tokens[idx + 1]
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        env[key] = value
    return env


def _extract_mounts_from_docker_cmd(tokens: list[str]) -> list[str]:
    mounts: list[str] = []
    for idx, token in enumerate(tokens):
        if token != "--mount" or idx + 1 >= len(tokens):
            continue
        mounts.append(tokens[idx + 1])
    return mounts


def _extract_user_from_docker_cmd(tokens: list[str]) -> str | None:
    for idx, token in enumerate(tokens):
        if token != "--user" or idx + 1 >= len(tokens):
            continue
        return tokens[idx + 1]
    return None


def test_build_docker_run_cmd_includes_skillkit_compat_env(monkeypatch, tmp_path):
    from app.worker import sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(sandbox_mod.settings, "LLM_BASE_URL", "http://127.0.0.1:8317")
    monkeypatch.setattr(sandbox_mod.settings, "LLM_MODEL", "gpt-5.3-codex")
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_AGENT_PORT", 19090)
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_DUMP_MODEL_API_RESPONSE", True)
    raw_log_dir = tmp_path / "model_api_logs"
    monkeypatch.setattr(
        sandbox_mod.settings,
        "SANDBOX_MODEL_API_RAW_LOG_HOST_DIR",
        str(raw_log_dir),
    )
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_GRADLE_CMD_TIMEOUT_SECONDS", 480)
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_GRADLE_USER_HOME", "/workspace/.gradle")
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_GRADLE_WRAPPER_PREWARM", True)
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_GRADLE_WRAPPER_PREWARM_TIMEOUT_SECONDS", 180)

    backend = DockerSandboxBackend()
    cmd = backend._build_docker_run_cmd(
        container_name="sbx-test",
        image="sandbox-image:latest",
        workspace="/tmp/workspace",
        task_id="task-123",
    )
    env = _extract_env_vars_from_docker_cmd(cmd)
    mounts = _extract_mounts_from_docker_cmd(cmd)

    assert env["LLM_API_KEY"] == "test-key"
    assert env["LLM_BASE_URL"] == "http://127.0.0.1:8317"
    assert env["LLM_MODEL"] == "gpt-5.3-codex"
    assert env["OPENAI_API_KEY"] == "test-key"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8317/v1"
    assert env["MINIMAX_MODEL"] == "gpt-5.3-codex"
    assert env["AGENT_PORT"] == "19090"
    assert env["SANDBOX_DUMP_MODEL_API_RESPONSE"] == "true"
    assert env["SANDBOX_MODEL_API_RAW_LOG_PATH"] == "/model_api_logs/task-123.jsonl"
    assert env["SANDBOX_GRADLE_CMD_TIMEOUT_SECONDS"] == "480"
    assert env["GRADLE_USER_HOME"] == "/workspace/.gradle"
    assert env["SANDBOX_GRADLE_WRAPPER_PREWARM"] == "true"
    assert env["SANDBOX_GRADLE_WRAPPER_PREWARM_TIMEOUT_SECONDS"] == "180"
    assert f"type=bind,src={raw_log_dir},dst=/model_api_logs" in mounts


def test_build_docker_run_cmd_disables_raw_model_dump_when_config_off(monkeypatch, tmp_path):
    from app.worker import sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(sandbox_mod.settings, "LLM_BASE_URL", "http://127.0.0.1:8317")
    monkeypatch.setattr(sandbox_mod.settings, "LLM_MODEL", "gpt-5.3-codex")
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_AGENT_PORT", 19090)
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_DUMP_MODEL_API_RESPONSE", False)
    monkeypatch.setattr(
        sandbox_mod.settings,
        "SANDBOX_MODEL_API_RAW_LOG_HOST_DIR",
        str(tmp_path / "model_api_logs"),
    )

    backend = DockerSandboxBackend()
    cmd = backend._build_docker_run_cmd(
        container_name="sbx-test",
        image="sandbox-image:latest",
        workspace="/tmp/workspace",
        task_id="task-123",
    )
    env = _extract_env_vars_from_docker_cmd(cmd)
    mounts = _extract_mounts_from_docker_cmd(cmd)

    assert env["SANDBOX_DUMP_MODEL_API_RESPONSE"] == "false"
    assert "SANDBOX_MODEL_API_RAW_LOG_PATH" not in env
    assert not any(mount.endswith("dst=/model_api_logs") for mount in mounts)


def test_build_docker_run_cmd_uses_workspace_owner_by_default(monkeypatch):
    from app.worker import sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_RUN_AS_WORKSPACE_OWNER", True, raising=False)

    backend = DockerSandboxBackend()
    cmd = backend._build_docker_run_cmd(
        container_name="sbx-test",
        image="sandbox-image:latest",
        workspace="/tmp/workspace",
        task_id="task-123",
        workspace_uid=1234,
        workspace_gid=2345,
    )

    assert _extract_user_from_docker_cmd(cmd) == "1234:2345"


def test_build_docker_run_cmd_skips_workspace_owner_when_disabled(monkeypatch):
    from app.worker import sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_RUN_AS_WORKSPACE_OWNER", False, raising=False)

    backend = DockerSandboxBackend()
    cmd = backend._build_docker_run_cmd(
        container_name="sbx-test",
        image="sandbox-image:latest",
        workspace="/tmp/workspace",
        task_id="task-123",
        workspace_uid=1234,
        workspace_gid=2345,
    )

    assert _extract_user_from_docker_cmd(cmd) is None


def test_coding_sandbox_image_provides_java_toolchain():
    dockerfile_path = Path(__file__).resolve().parents[1] / "sandbox" / "Dockerfile.coding"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "FROM eclipse-temurin:8-jdk AS jdk8" in content
    assert "FROM eclipse-temurin:17-jdk AS jdk17" in content
    assert "JAVA8_HOME" in content
    assert "JAVA17_HOME" in content
    assert "ENV JAVA_HOME=/opt/jdk17" in content


def test_base_sandbox_image_makes_runtime_entrypoints_world_readable():
    dockerfile_path = Path(__file__).resolve().parents[1] / "sandbox" / "Dockerfile.base"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "COPY --chown=agent:agent sandbox/agent_server.py /app/agent_server.py" in content
    assert "COPY --chown=agent:agent sandbox/tool_policy.py /app/tool_policy.py" in content
    assert "RUN chmod -R a+rX /app /skills" in content


def test_base_sandbox_image_copies_skills_with_agent_ownership():
    dockerfile_path = Path(__file__).resolve().parents[1] / "sandbox" / "Dockerfile.base"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "COPY --chown=agent:agent skills/ /skills/" in content
