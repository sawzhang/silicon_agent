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


def test_coding_sandbox_image_provides_java_toolchain():
    dockerfile_path = Path(__file__).resolve().parents[1] / "sandbox" / "Dockerfile.coding"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "FROM eclipse-temurin:8-jdk AS jdk8" in content
    assert "FROM eclipse-temurin:17-jdk AS jdk17" in content
    assert "JAVA8_HOME" in content
    assert "JAVA17_HOME" in content
    assert "ENV JAVA_HOME=/opt/jdk17" in content
