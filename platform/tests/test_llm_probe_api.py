from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
