from __future__ import annotations

import time
from datetime import datetime

import httpx

from app.config import settings
from app.integration.llm_client import ChatMessage, LLMClient
from app.schemas.llm_probe import LLMProbeResponse


class LLMProbeService:
    """Service for low-cost, low-latency LLM liveness checks."""

    def _build_client(self, timeout_ms: int) -> LLMClient:
        """Create a short-lived client so probe timeout does not affect global client state."""
        timeout_sec = max(timeout_ms / 1000.0, 0.1)
        return LLMClient(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout=timeout_sec,
        )

    async def probe(self, timeout_ms: int = 3000) -> LLMProbeResponse:
        """Run a minimal chat-completions request and return normalized diagnostics."""
        requested_model = settings.LLM_MODEL
        started = time.perf_counter()
        checked_at = datetime.now()
        client = self._build_client(timeout_ms)

        try:
            llm_response = await client.chat(
                messages=[ChatMessage(role="user", content="ping")],
                model=settings.LLM_MODEL,
                temperature=0.0,
                max_tokens=8,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return LLMProbeResponse(
                ok=True,
                provider="openai-compatible",
                base_url=settings.LLM_BASE_URL.rstrip("/"),
                requested_model=requested_model,
                resolved_model=llm_response.model,
                latency_ms=latency_ms,
                input_tokens=llm_response.input_tokens,
                output_tokens=llm_response.output_tokens,
                total_tokens=llm_response.total_tokens,
                checked_at=checked_at,
            )
        except httpx.TimeoutException:
            return self._failed_response(
                requested_model=requested_model,
                started=started,
                checked_at=checked_at,
                error_code="UPSTREAM_TIMEOUT",
                error_message="LLM probe timeout",
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in (401, 403):
                error_code = "UPSTREAM_AUTH_ERROR"
            elif status_code == 400:
                error_code = "UPSTREAM_BAD_REQUEST"
            elif status_code >= 500:
                error_code = "UPSTREAM_SERVER_ERROR"
            else:
                error_code = "PROBE_INTERNAL_ERROR"
            return self._failed_response(
                requested_model=requested_model,
                started=started,
                checked_at=checked_at,
                error_code=error_code,
                error_message=f"LLM probe failed with status {status_code}",
            )
        except Exception as exc:
            return self._failed_response(
                requested_model=requested_model,
                started=started,
                checked_at=checked_at,
                error_code="PROBE_INTERNAL_ERROR",
                error_message=str(exc) or "LLM probe internal error",
            )
        finally:
            await client.close()

    @staticmethod
    def _failed_response(
        *,
        requested_model: str,
        started: float,
        checked_at: datetime,
        error_code: str,
        error_message: str,
    ) -> LLMProbeResponse:
        """Build a standardized probe failure payload."""
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMProbeResponse(
            ok=False,
            provider="openai-compatible",
            base_url=settings.LLM_BASE_URL.rstrip("/"),
            requested_model=requested_model,
            resolved_model=None,
            latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            error_code=error_code,
            error_message=error_message,
            checked_at=checked_at,
        )
