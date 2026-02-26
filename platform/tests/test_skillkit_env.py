from app.integration.skillkit_env import (
    build_sandbox_llm_env,
    derive_skillkit_env,
    hydrate_skillkit_env,
    normalize_openai_base_url,
)


def test_normalize_openai_base_url_appends_v1_once():
    assert normalize_openai_base_url("http://127.0.0.1:8317") == "http://127.0.0.1:8317/v1"
    assert normalize_openai_base_url("http://127.0.0.1:8317/") == "http://127.0.0.1:8317/v1"
    assert normalize_openai_base_url("http://127.0.0.1:8317/v1") == "http://127.0.0.1:8317/v1"
    assert normalize_openai_base_url("http://127.0.0.1:8317/v1/") == "http://127.0.0.1:8317/v1"


def test_derive_skillkit_env_skips_empty_values():
    derived = derive_skillkit_env(
        llm_api_key="",
        llm_base_url="http://gateway:8317",
        llm_model="",
    )
    assert derived == {"OPENAI_BASE_URL": "http://gateway:8317/v1"}


def test_hydrate_skillkit_env_preserves_existing_values_by_default():
    env = {
        "OPENAI_API_KEY": "preset",
        "OPENAI_BASE_URL": "http://preset/v1",
    }
    applied = hydrate_skillkit_env(
        env,
        llm_api_key="llm-key",
        llm_base_url="http://llm-host:8317",
        llm_model="gpt-5.3-codex",
    )
    assert env["OPENAI_API_KEY"] == "preset"
    assert env["OPENAI_BASE_URL"] == "http://preset/v1"
    assert env["MINIMAX_MODEL"] == "gpt-5.3-codex"
    assert applied == {"MINIMAX_MODEL": "gpt-5.3-codex"}


def test_build_sandbox_llm_env_contains_platform_and_skillkit_keys():
    env = build_sandbox_llm_env(
        llm_api_key="test-key",
        llm_base_url="http://127.0.0.1:8317",
        llm_model="gpt-5.3-codex",
        agent_port=9090,
    )
    assert env["LLM_API_KEY"] == "test-key"
    assert env["LLM_BASE_URL"] == "http://127.0.0.1:8317"
    assert env["LLM_MODEL"] == "gpt-5.3-codex"
    assert env["AGENT_PORT"] == "9090"
    assert env["OPENAI_API_KEY"] == "test-key"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8317/v1"
    assert env["MINIMAX_MODEL"] == "gpt-5.3-codex"
