"""Async LLM client for OpenAI-compatible chat completions API."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model: str


class LLMClient:
    """Async client for OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        model = model or settings.LLM_MODEL
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = f"{self._base_url}/v1/chat/completions"
        logger.debug("LLM request: model=%s, messages=%d", model, len(messages))

        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

        return LLMResponse(
            content=choice["message"]["content"],
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            model=data.get("model", model),
        )

    async def close(self) -> None:
        await self._client.aclose()


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get the module-level LLM client singleton."""
    global _client
    if _client is None:
        _client = LLMClient(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout=settings.LLM_TIMEOUT,
        )
    return _client


async def close_llm_client() -> None:
    """Close the module-level LLM client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
        logger.info("LLM client closed")
