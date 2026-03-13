from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.dependencies import get_llm_probe_service
from app.main import app
from app.schemas.llm_probe import LLMProbeResponse


class _FakeProbeService:
    def __init__(self, response: LLMProbeResponse):
        self._response = response

    async def probe(self, timeout_ms=3000):
        return self._response


@pytest.mark.asyncio
async def test_llm_probe_api_success(client):
    fake = _FakeProbeService(
        LLMProbeResponse(
            ok=True,
            provider="openai-compatible",
            base_url="https://example.com",
            requested_model="gpt-5.3-codex",
            resolved_model="gpt-5.3-codex",
            latency_ms=123,
            input_tokens=2,
            output_tokens=1,
            total_tokens=3,
            checked_at=datetime.now(timezone.utc),
        )
    )
    app.dependency_overrides[get_llm_probe_service] = lambda: fake
    try:
        resp = await client.get("/api/v1/llm/probe", params={"timeout_ms": 1200})
    finally:
        app.dependency_overrides.pop(get_llm_probe_service, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["resolved_model"] == "gpt-5.3-codex"


@pytest.mark.asyncio
async def test_llm_probe_api_invalid_timeout(client):
    resp = await client.get("/api/v1/llm/probe", params={"timeout_ms": 100})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_llm_probe_api_failure_payload(client):
    fake = _FakeProbeService(
        LLMProbeResponse(
            ok=False,
            provider="openai-compatible",
            base_url="https://example.com",
            requested_model="gpt-5.3-codex",
            resolved_model=None,
            latency_ms=3000,
            error_code="UPSTREAM_TIMEOUT",
            error_message="LLM probe timeout",
            checked_at=datetime.now(timezone.utc),
        )
    )
    app.dependency_overrides[get_llm_probe_service] = lambda: fake
    try:
        resp = await client.get("/api/v1/llm/probe")
    finally:
        app.dependency_overrides.pop(get_llm_probe_service, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error_code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.asyncio
async def test_get_llm_config(client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-abcd1234efgh5678")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.test.com/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4o")
    monkeypatch.setattr(settings, "LLM_TIMEOUT", 30)
    monkeypatch.setattr(settings, "LLM_ROLE_MODEL_MAP", '{"coding": "deepseek-coder"}')

    resp = await client.get("/api/v1/llm/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_key_set"] is True
    assert data["api_key_masked"] == "sk-a****5678"
    assert data["base_url"] == "https://api.test.com/v1"
    assert data["model"] == "gpt-4o"
    assert data["role_model_map"] == {"coding": "deepseek-coder"}


@pytest.mark.asyncio
async def test_get_llm_config_empty_key(client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    resp = await client.get("/api/v1/llm/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_key_set"] is False
    assert data["api_key_masked"] == ""


@pytest.mark.asyncio
async def test_update_llm_config(client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "old-key")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://old.com/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", "old-model")
    monkeypatch.setattr(settings, "LLM_TIMEOUT", 10)
    monkeypatch.setattr(settings, "LLM_ROLE_MODEL_MAP", "{}")

    resp = await client.put(
        "/api/v1/llm/config",
        json={"base_url": "https://new.com/v1/", "model": "gpt-4o-mini"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_url"] == "https://new.com/v1"  # trailing slash stripped
    assert data["model"] == "gpt-4o-mini"
    # api_key unchanged
    assert data["api_key_masked"] != ""


@pytest.mark.asyncio
async def test_update_llm_config_api_key(client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://x.com/v1")
    monkeypatch.setattr(settings, "LLM_MODEL", "m")
    monkeypatch.setattr(settings, "LLM_TIMEOUT", 10)
    monkeypatch.setattr(settings, "LLM_ROLE_MODEL_MAP", "{}")

    resp = await client.put(
        "/api/v1/llm/config",
        json={"api_key": "sk-new-key-value-1234"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_key_set"] is True
    assert "new-" not in data["api_key_masked"]  # key is masked
