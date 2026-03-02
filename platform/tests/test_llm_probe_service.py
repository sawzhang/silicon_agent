from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from app.config import settings
from app.integration.llm_client import LLMResponse
from app.services.llm_probe_service import LLMProbeService


@dataclass
class _FakeClient:
    response: LLMResponse | None = None
    exc: Exception | None = None

    async def chat(self, **kwargs):
        if self.exc is not None:
            raise self.exc
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 8
        assert kwargs["model"] == settings.LLM_MODEL
        return self.response

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_probe_success(monkeypatch):
    service = LLMProbeService()
    fake_client = _FakeClient(
        response=LLMResponse(
            content="pong",
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
            model="gpt-5.3-codex",
        )
    )
    monkeypatch.setattr(service, "_build_client", lambda timeout_ms: fake_client)

    result = await service.probe(timeout_ms=1800)

    assert result.ok is True
    assert result.requested_model == settings.LLM_MODEL
    assert result.resolved_model == "gpt-5.3-codex"
    assert result.total_tokens == 5
    assert result.error_code is None


@pytest.mark.asyncio
async def test_probe_timeout(monkeypatch):
    service = LLMProbeService()
    fake_client = _FakeClient(exc=httpx.TimeoutException("timeout"))
    monkeypatch.setattr(service, "_build_client", lambda timeout_ms: fake_client)

    result = await service.probe(timeout_ms=1000)

    assert result.ok is False
    assert result.error_code == "UPSTREAM_TIMEOUT"
    assert result.total_tokens == 0


@pytest.mark.asyncio
async def test_probe_http_status_mapping(monkeypatch):
    service = LLMProbeService()
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp = httpx.Response(status_code=401, request=req)
    fake_client = _FakeClient(exc=httpx.HTTPStatusError("unauthorized", request=req, response=resp))
    monkeypatch.setattr(service, "_build_client", lambda timeout_ms: fake_client)

    result = await service.probe(timeout_ms=1000)

    assert result.ok is False
    assert result.error_code == "UPSTREAM_AUTH_ERROR"
    assert "401" in (result.error_message or "")
