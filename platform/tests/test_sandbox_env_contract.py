from app.worker.sandbox import SandboxManager


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


def test_build_docker_run_cmd_includes_skillkit_compat_env(monkeypatch):
    from app.worker import sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(sandbox_mod.settings, "LLM_BASE_URL", "http://127.0.0.1:8317")
    monkeypatch.setattr(sandbox_mod.settings, "LLM_MODEL", "gpt-5.3-codex")
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_AGENT_PORT", 19090)

    manager = SandboxManager()
    cmd = manager._build_docker_run_cmd(
        container_name="sbx-test",
        image="sandbox-image:latest",
        workspace="/tmp/workspace",
    )
    env = _extract_env_vars_from_docker_cmd(cmd)

    assert env["LLM_API_KEY"] == "test-key"
    assert env["LLM_BASE_URL"] == "http://127.0.0.1:8317"
    assert env["LLM_MODEL"] == "gpt-5.3-codex"
    assert env["OPENAI_API_KEY"] == "test-key"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8317/v1"
    assert env["MINIMAX_MODEL"] == "gpt-5.3-codex"
    assert env["AGENT_PORT"] == "19090"
