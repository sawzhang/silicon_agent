"""Simple JWT token issuing endpoint for development/internal use."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    username: str
    password: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/token", response_model=TokenResponse)
async def issue_token(request: TokenRequest):
    """Issue a JWT token.

    In production this should validate against a real user store.
    Currently accepts any username when JWT_ENABLED is false (dev mode).
    When JWT_ENABLED is true, rejects empty passwords as a minimal guard.
    """
    if settings.JWT_ENABLED and not request.password:
        raise HTTPException(status_code=401, detail="Password required when JWT is enabled")

    expires_delta = timedelta(hours=24)
    expire = datetime.now(timezone.utc) + expires_delta

    payload = {
        "sub": request.username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    return TokenResponse(
        access_token=token,
        expires_in=int(expires_delta.total_seconds()),
    )
