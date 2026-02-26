"""SkillKit runtime environment compatibility helpers.

The platform treats ``LLM_*`` as canonical configuration, while SkillKit's
``AgentConfig.from_env()`` currently reads ``OPENAI_*`` and ``MINIMAX_MODEL``.
This module centralizes translation so host and sandbox execution paths stay
consistent and drift-free.
"""

from __future__ import annotations

from collections.abc import MutableMapping


def normalize_openai_base_url(base_url: str | None) -> str:
    """Normalize base URL for OpenAI-compatible clients.

    Input may come from ``LLM_BASE_URL`` which usually omits ``/v1`` because
    platform ``LLMClient`` appends it during request construction.
    """
    value = (base_url or "").strip()
    if not value:
        return ""
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def derive_skillkit_env(
    *,
    llm_api_key: str | None,
    llm_base_url: str | None,
    llm_model: str | None,
) -> dict[str, str]:
    """Derive SkillKit-compatible env vars from canonical ``LLM_*`` values."""
    env: dict[str, str] = {}
    key = (llm_api_key or "").strip()
    if key:
        env["OPENAI_API_KEY"] = key
    normalized_base = normalize_openai_base_url(llm_base_url)
    if normalized_base:
        env["OPENAI_BASE_URL"] = normalized_base
    model = (llm_model or "").strip()
    if model:
        env["MINIMAX_MODEL"] = model
    return env


def hydrate_skillkit_env(
    environ: MutableMapping[str, str],
    *,
    llm_api_key: str | None,
    llm_base_url: str | None,
    llm_model: str | None,
    override: bool = False,
) -> dict[str, str]:
    """Hydrate missing SkillKit env keys in a process environment.

    Returns the keys that were set/updated.
    """
    derived = derive_skillkit_env(
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
    )
    applied: dict[str, str] = {}
    for key, value in derived.items():
        if override or not environ.get(key):
            environ[key] = value
            applied[key] = value
    return applied


def build_sandbox_llm_env(
    *,
    llm_api_key: str | None,
    llm_base_url: str | None,
    llm_model: str | None,
    agent_port: int | str,
) -> dict[str, str]:
    """Build the complete LLM env payload for sandbox containers."""
    env = {
        "LLM_API_KEY": (llm_api_key or "").strip(),
        "LLM_BASE_URL": (llm_base_url or "").strip(),
        "LLM_MODEL": (llm_model or "").strip(),
        "AGENT_PORT": str(agent_port),
    }
    env.update(
        derive_skillkit_env(
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )
    )
    return env
