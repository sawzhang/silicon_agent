"""Tests for JWT auth middleware and error handler middleware."""
import time

import jwt
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.middleware.error_handler import ErrorHandlerMiddleware


# ---------------------------------------------------------------------------
# JWT Auth Middleware tests (using the shared `client` fixture which targets
# the full app, JWT_ENABLED=False by default in conftest.py)
# ---------------------------------------------------------------------------


async def test_jwt_disabled_passes_through(client):
    """JWT_ENABLED=False — requests pass through without any token check."""
    assert settings.JWT_ENABLED is False
    resp = await client.get("/health")
    assert resp.status_code == 200


async def test_jwt_missing_auth_header(monkeypatch, client):
    """JWT_ENABLED=True, no Authorization header → 401."""
    monkeypatch.setattr(settings, "JWT_ENABLED", True)
    try:
        resp = await client.get("/api/v1/tasks")
        assert resp.status_code == 401
        assert "Missing or invalid" in resp.json()["detail"]
    finally:
        monkeypatch.setattr(settings, "JWT_ENABLED", False)


async def test_jwt_malformed_bearer(monkeypatch, client):
    """JWT_ENABLED=True, 'Token xyz' instead of 'Bearer ...' → 401."""
    monkeypatch.setattr(settings, "JWT_ENABLED", True)
    try:
        resp = await client.get("/api/v1/tasks", headers={"Authorization": "Token somevalue"})
        assert resp.status_code == 401
        assert "Missing or invalid" in resp.json()["detail"]
    finally:
        monkeypatch.setattr(settings, "JWT_ENABLED", False)


async def test_jwt_valid_token_passes(monkeypatch, client):
    """JWT_ENABLED=True, valid HS256 token → request succeeds (200)."""
    monkeypatch.setattr(settings, "JWT_ENABLED", True)
    try:
        token = jwt.encode(
            {"sub": "testuser", "exp": int(time.time()) + 3600},
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        resp = await client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
    finally:
        monkeypatch.setattr(settings, "JWT_ENABLED", False)


async def test_jwt_expired_token(monkeypatch, client):
    """JWT_ENABLED=True, expired token → 401 with 'expired' in detail."""
    monkeypatch.setattr(settings, "JWT_ENABLED", True)
    try:
        token = jwt.encode(
            {"sub": "testuser", "exp": int(time.time()) - 10},
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        resp = await client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()
    finally:
        monkeypatch.setattr(settings, "JWT_ENABLED", False)


async def test_jwt_invalid_signature(monkeypatch, client):
    """JWT_ENABLED=True, bad signature → 401 with 'Invalid' in detail."""
    monkeypatch.setattr(settings, "JWT_ENABLED", True)
    try:
        token = jwt.encode(
            {"sub": "testuser", "exp": int(time.time()) + 3600},
            "wrong-secret",
            algorithm="HS256",
        )
        resp = await client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]
    finally:
        monkeypatch.setattr(settings, "JWT_ENABLED", False)


async def test_jwt_exempt_path(monkeypatch, client):
    """/health is exempt — no token needed even when JWT is enabled."""
    monkeypatch.setattr(settings, "JWT_ENABLED", True)
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
    finally:
        monkeypatch.setattr(settings, "JWT_ENABLED", False)


# ---------------------------------------------------------------------------
# ErrorHandlerMiddleware tests — use a standalone mini FastAPI app so we can
# control DEBUG without affecting the shared test app.
# ---------------------------------------------------------------------------


def _make_error_app(debug: bool) -> FastAPI:
    """Build a tiny FastAPI app that always raises a RuntimeError."""
    mini = FastAPI()
    mini.add_middleware(ErrorHandlerMiddleware)

    @mini.get("/boom")
    async def boom():
        raise RuntimeError("something went wrong")

    # Patch settings.DEBUG for this mini-app via import
    import app.config as cfg_mod
    cfg_mod.settings.DEBUG = debug
    return mini


async def test_error_handler_returns_500():
    """ErrorHandlerMiddleware wraps unhandled exceptions → 500."""
    import app.config as cfg_mod
    original_debug = cfg_mod.settings.DEBUG
    cfg_mod.settings.DEBUG = False
    try:
        mini = FastAPI()
        mini.add_middleware(ErrorHandlerMiddleware)

        @mini.get("/boom")
        async def boom():
            raise RuntimeError("something went wrong")

        transport = ASGITransport(app=mini)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"] == "Internal server error"
        # Non-debug: error field should be the generic message
        assert body["error"] == "Internal server error"
    finally:
        cfg_mod.settings.DEBUG = original_debug


async def test_error_handler_debug_mode():
    """DEBUG=True exposes the actual exception message in the 'error' field."""
    import app.config as cfg_mod
    original_debug = cfg_mod.settings.DEBUG
    cfg_mod.settings.DEBUG = True
    try:
        mini = FastAPI()
        mini.add_middleware(ErrorHandlerMiddleware)

        @mini.get("/boom")
        async def boom():
            raise RuntimeError("secret internal detail")

        transport = ASGITransport(app=mini)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert "secret internal detail" in body["error"]
    finally:
        cfg_mod.settings.DEBUG = original_debug
