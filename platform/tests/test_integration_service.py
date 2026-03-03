"""Unit tests for IntegrationService using AsyncMock sessions."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models.integration import ProjectIntegrationModel
from app.schemas.integration import IntegrationCreateRequest, IntegrationUpdateRequest
from app.services.integration_service import IntegrationService


def _mock_result(scalar=None, scalar_one_or_none=None, scalars_all=None):
    r = MagicMock()
    r.scalar.return_value = scalar
    r.scalar_one_or_none.return_value = scalar_one_or_none
    sm = MagicMock()
    sm.all.return_value = scalars_all or []
    r.scalars.return_value = sm
    return r


def _make_integration(
    project_id="proj-1",
    provider="github",
    webhook_secret="abc123",
    access_token="ghp_xxxxxxxxyyyy",
    enabled=True,
):
    m = MagicMock(spec=ProjectIntegrationModel)
    m.id = "int-1"
    m.project_id = project_id
    m.provider = provider
    m.webhook_secret = webhook_secret
    m.access_token = access_token
    m.extra_config = None
    m.enabled = enabled
    m.created_at = "2025-01-01T00:00:00"
    m.updated_at = "2025-01-01T00:00:00"
    return m


# ── mask_token ────────────────────────────────────────────────


class TestMaskToken:
    def test_none(self):
        assert IntegrationService._mask_token(None) is None

    def test_empty(self):
        assert IntegrationService._mask_token("") is None

    def test_short(self):
        assert IntegrationService._mask_token("abc") == "****"

    def test_normal(self):
        assert IntegrationService._mask_token("ghp_xxxxxxxxyyyy") == "****yyyy"


# ── list_integrations ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_integrations_empty():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _mock_result(scalar_one_or_none="proj-1"),  # _check_project_exists
            _mock_result(scalars_all=[]),                # list query
        ]
    )
    svc = IntegrationService(session)
    result = await svc.list_integrations("proj-1")
    assert result == []


@pytest.mark.asyncio
async def test_list_integrations_with_data():
    integration = _make_integration()
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _mock_result(scalar_one_or_none="proj-1"),
            _mock_result(scalars_all=[integration]),
        ]
    )
    svc = IntegrationService(session)
    result = await svc.list_integrations("proj-1")
    assert len(result) == 1
    assert result[0].provider == "github"
    assert result[0].webhook_url == "/webhooks/github/proj-1"
    assert result[0].access_token == "****yyyy"


@pytest.mark.asyncio
async def test_list_integrations_project_not_found():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=None)
    )
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.list_integrations("nonexistent")
    assert exc_info.value.status_code == 404


# ── create_integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_integration_success():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none="proj-1")
    )

    async def fake_refresh(obj):
        obj.id = "int-new"
        obj.project_id = "proj-1"
        obj.provider = "github"
        obj.webhook_secret = "secret123"
        obj.access_token = None
        obj.extra_config = None
        obj.enabled = True
        obj.created_at = "2025-01-01T00:00:00"
        obj.updated_at = "2025-01-01T00:00:00"

    session.refresh = AsyncMock(side_effect=fake_refresh)
    svc = IntegrationService(session)

    result = await svc.create_integration(
        "proj-1",
        IntegrationCreateRequest(provider="github"),
    )
    assert result.provider == "github"
    assert result.webhook_url == "/webhooks/github/proj-1"
    session.add.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_create_integration_invalid_provider():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none="proj-1")
    )
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.create_integration(
            "proj-1",
            IntegrationCreateRequest(provider="bitbucket"),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_integration_duplicate():
    from sqlalchemy.exc import IntegrityError
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none="proj-1")
    )
    session.commit = AsyncMock(side_effect=IntegrityError("dup", params=None, orig=None))
    session.rollback = AsyncMock()
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.create_integration(
            "proj-1",
            IntegrationCreateRequest(provider="github"),
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_integration_project_not_found():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=None)
    )
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.create_integration(
            "nonexistent",
            IntegrationCreateRequest(provider="github"),
        )
    assert exc_info.value.status_code == 404


# ── update_integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_integration_success():
    integration = _make_integration()
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=integration)
    )
    session.refresh = AsyncMock()
    svc = IntegrationService(session)
    result = await svc.update_integration(
        "proj-1", "github",
        IntegrationUpdateRequest(enabled=False),
    )
    assert result.provider == "github"
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_update_integration_not_found():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=None)
    )
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.update_integration(
            "proj-1", "github",
            IntegrationUpdateRequest(enabled=False),
        )
    assert exc_info.value.status_code == 404


# ── delete_integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_integration_success():
    integration = _make_integration()
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=integration)
    )
    svc = IntegrationService(session)
    await svc.delete_integration("proj-1", "github")
    session.delete.assert_called_once_with(integration)
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_integration_not_found():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=None)
    )
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.delete_integration("proj-1", "github")
    assert exc_info.value.status_code == 404


# ── regenerate_secret ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_regenerate_secret_success():
    integration = _make_integration(webhook_secret="old_secret")
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=integration)
    )
    session.refresh = AsyncMock()
    svc = IntegrationService(session)
    result = await svc.regenerate_secret("proj-1", "github")
    # webhook_secret should have been changed
    assert integration.webhook_secret != "old_secret"
    assert result.provider == "github"
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_regenerate_secret_not_found():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=None)
    )
    svc = IntegrationService(session)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await svc.regenerate_secret("proj-1", "github")
    assert exc_info.value.status_code == 404


# ── get_integration_by_project_provider (internal) ────────────


@pytest.mark.asyncio
async def test_get_integration_by_project_provider():
    integration = _make_integration()
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=integration)
    )
    svc = IntegrationService(session)
    result = await svc.get_integration_by_project_provider("proj-1", "github")
    assert result is integration


@pytest.mark.asyncio
async def test_get_integration_by_project_provider_none():
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_mock_result(scalar_one_or_none=None)
    )
    svc = IntegrationService(session)
    result = await svc.get_integration_by_project_provider("proj-1", "github")
    assert result is None
