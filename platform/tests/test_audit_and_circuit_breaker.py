"""Mock-session unit tests for audit_service.py and circuit_breaker_service.py.

All session.execute() / session.commit() / session.refresh() calls are mocked
so every line after `await` is reached within the same trace context — fixing
the coverage.py / Python 3.13 sys.monitoring coroutine-resume blind-spot.

Covered lines:
  audit_service.py    : 38-46, 57, 60
  circuit_breaker_service.py : 21-23, 40-41, 49-57
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.audit_service import AuditService
from app.services.circuit_breaker_service import CircuitBreakerService


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session() -> AsyncMock:
    """Return an AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    return session


_MISSING = object()


def _exec_result(*, scalar=None, scalars_list=None, scalar_one_or_none=_MISSING):
    """Build a mock result object returned by session.execute()."""
    r = MagicMock()
    r.scalar.return_value = scalar

    sm = MagicMock()
    sm.all.return_value = scalars_list if scalars_list is not None else []
    r.scalars.return_value = sm

    if scalar_one_or_none is not _MISSING:
        r.scalar_one_or_none.return_value = scalar_one_or_none
    else:
        r.scalar_one_or_none.return_value = None

    return r


def _make_audit_log(
    *,
    id: str | None = None,
    agent_role: str = "coding",
    action_type: str = "write",
    risk_level: str = "low",
    action_detail: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id or str(uuid.uuid4()),
        agent_role=agent_role,
        action_type=action_type,
        risk_level=risk_level,
        action_detail=action_detail or {},
        created_at=datetime.now(timezone.utc),
    )


def _make_circuit_breaker(
    *,
    id: str | None = None,
    level: int = 1,
    status: str = "triggered",
    triggered_by: str = "test-suite",
    trigger_reason: str = "Cost limit",
    triggered_at: datetime | None = None,
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id or str(uuid.uuid4()),
        level=level,
        status=status,
        triggered_by=triggered_by,
        trigger_reason=trigger_reason,
        triggered_at=triggered_at or datetime.now(timezone.utc),
        resolved_at=resolved_at,
        resolved_by=resolved_by,
    )


# ── AuditService.list_logs ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_list_logs_empty():
    """Lines 38-46: list_logs with no results returns empty AuditLogListResponse."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=0),          # count query
        _exec_result(scalars_list=[]),   # list query
    ]

    svc = AuditService(session)
    result = await svc.list_logs()

    assert result.total == 0
    assert result.items == []
    assert result.page == 1
    assert result.page_size == 20
    assert session.execute.call_count == 2


@pytest.mark.asyncio
async def test_audit_list_logs_with_results():
    """Lines 38-46: list_logs returns populated AuditLogListResponse."""
    log1 = _make_audit_log(agent_role="coding", action_type="write", risk_level="low")
    log2 = _make_audit_log(agent_role="review", action_type="read", risk_level="high")

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=2),                  # count = 2
        _exec_result(scalars_list=[log1, log2]), # log items
    ]

    with patch("app.services.audit_service.AuditLogResponse.model_validate") as mock_validate:
        # Return a simple response object for each log
        mock_validate.side_effect = lambda obj: SimpleNamespace(
            id=obj.id,
            agent_role=obj.agent_role,
            action_type=obj.action_type,
            risk_level=obj.risk_level,
            action_detail=obj.action_detail,
            created_at=obj.created_at,
        )
        svc = AuditService(session)
        result = await svc.list_logs(page=1, page_size=20)

    assert result.total == 2
    assert len(result.items) == 2
    assert result.page == 1
    assert result.page_size == 20


@pytest.mark.asyncio
async def test_audit_list_logs_with_filters():
    """Lines 27-44: filters applied before executing both queries."""
    log = _make_audit_log(agent_role="test", action_type="execute", risk_level="high")

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=1),
        _exec_result(scalars_list=[log]),
    ]

    with patch("app.services.audit_service.AuditLogResponse.model_validate") as mock_validate:
        mock_validate.side_effect = lambda obj: obj
        svc = AuditService(session)
        result = await svc.list_logs(
            agent_role="test", risk_level="high", action_type="execute"
        )

    assert result.total == 1
    assert session.execute.call_count == 2


@pytest.mark.asyncio
async def test_audit_list_logs_count_none_defaults_zero():
    """Line 38: scalar() returning None → total defaults to 0."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=None),       # count returns None
        _exec_result(scalars_list=[]),
    ]

    svc = AuditService(session)
    result = await svc.list_logs()

    assert result.total == 0


@pytest.mark.asyncio
async def test_audit_list_logs_pagination_params():
    """Lines 41-50: pagination offset/limit calculated, page/page_size echoed in response."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=100),
        _exec_result(scalars_list=[]),
    ]

    svc = AuditService(session)
    result = await svc.list_logs(page=3, page_size=10)

    assert result.page == 3
    assert result.page_size == 10


# ── AuditService.get_log ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_get_log_not_found():
    """Line 57: scalar_one_or_none() returns None → get_log returns None."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalar_one_or_none=None)

    svc = AuditService(session)
    result = await svc.get_log("nonexistent-id")

    assert result is None


@pytest.mark.asyncio
async def test_audit_get_log_found():
    """Lines 57, 60: scalar_one_or_none() returns log → get_log returns AuditLogResponse."""
    log = _make_audit_log(id="audit-log-1", agent_role="doc", action_type="write")

    session = _make_session()
    session.execute.return_value = _exec_result(scalar_one_or_none=log)

    with patch("app.services.audit_service.AuditLogResponse.model_validate") as mock_validate:
        expected = SimpleNamespace(
            id=log.id,
            agent_role=log.agent_role,
            action_type=log.action_type,
            risk_level=log.risk_level,
            action_detail=log.action_detail,
            created_at=log.created_at,
        )
        mock_validate.return_value = expected
        svc = AuditService(session)
        result = await svc.get_log("audit-log-1")

    assert result is not None
    assert result.id == "audit-log-1"
    assert result.agent_role == "doc"
    mock_validate.assert_called_once_with(log)


# ── CircuitBreakerService.get_status ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_get_status_empty():
    """Lines 21-23: get_status with empty list returns CircuitBreakerListResponse with total=0."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalars_list=[])

    svc = CircuitBreakerService(session)
    result = await svc.get_status()

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_cb_get_status_with_items():
    """Lines 21-23: get_status with items returns populated response."""
    cb1 = _make_circuit_breaker(id="cb-1", level=1, status="triggered")
    cb2 = _make_circuit_breaker(id="cb-2", level=2, status="resolved")

    session = _make_session()
    session.execute.return_value = _exec_result(scalars_list=[cb1, cb2])

    with patch("app.services.circuit_breaker_service.CircuitBreakerResponse.model_validate") as mock_validate:
        mock_validate.side_effect = lambda obj: SimpleNamespace(
            id=obj.id,
            level=obj.level,
            status=obj.status,
            triggered_by=obj.triggered_by,
            trigger_reason=obj.trigger_reason,
            triggered_at=obj.triggered_at,
            resolved_at=obj.resolved_at,
            resolved_by=obj.resolved_by,
        )
        svc = CircuitBreakerService(session)
        result = await svc.get_status()

    assert result.total == 2
    assert len(result.items) == 2


# ── CircuitBreakerService.trigger ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_trigger_commits_and_refreshes():
    """Lines 40-41: trigger() calls session.commit() and session.refresh()."""
    session = _make_session()
    # After refresh, the cb object will have an id set via side_effect
    new_cb = _make_circuit_breaker(id="cb-new-1", level=1, status="triggered")

    async def fake_refresh(obj):
        obj.id = new_cb.id
        obj.status = new_cb.status
        obj.triggered_at = new_cb.triggered_at

    session.refresh.side_effect = fake_refresh

    with patch("app.services.circuit_breaker_service.CircuitBreakerResponse.model_validate") as mock_validate:
        mock_validate.return_value = SimpleNamespace(
            id="cb-new-1",
            level=1,
            status="triggered",
            triggered_by="test-suite",
            trigger_reason="Cost limit",
            triggered_at=new_cb.triggered_at,
            resolved_at=None,
            resolved_by=None,
        )
        svc = CircuitBreakerService(session)
        result = await svc.trigger(level=1, triggered_by="test-suite", reason="Cost limit")

    session.add.assert_called_once()
    session.commit.assert_called_once()
    session.refresh.assert_called_once()
    assert result.level == 1
    assert result.status == "triggered"


@pytest.mark.asyncio
async def test_cb_trigger_returns_response():
    """Lines 39-41: trigger() result reflects the created CircuitBreakerModel."""
    session = _make_session()
    cb_snapshot = _make_circuit_breaker(
        id="cb-snap", level=3, status="triggered",
        triggered_by="auto-monitor", trigger_reason="Token limit hit"
    )

    async def fake_refresh(obj):
        obj.id = cb_snapshot.id

    session.refresh.side_effect = fake_refresh

    with patch("app.services.circuit_breaker_service.CircuitBreakerResponse.model_validate") as mock_validate:
        mock_validate.return_value = SimpleNamespace(
            id=cb_snapshot.id,
            level=3,
            status="triggered",
            triggered_by="auto-monitor",
            trigger_reason="Token limit hit",
            triggered_at=cb_snapshot.triggered_at,
            resolved_at=None,
            resolved_by=None,
        )
        svc = CircuitBreakerService(session)
        result = await svc.trigger(level=3, triggered_by="auto-monitor", reason="Token limit hit")

    assert result.id == cb_snapshot.id
    assert result.triggered_by == "auto-monitor"


# ── CircuitBreakerService.resolve ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_resolve_not_found():
    """Lines 49-51: resolve() returns None when id not found."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalar_one_or_none=None)

    svc = CircuitBreakerService(session)
    result = await svc.resolve("nonexistent-id", resolved_by="ops")

    assert result is None
    session.commit.assert_not_called()
    session.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_cb_resolve_found_updates_fields():
    """Lines 49-57: resolve() updates status/resolved_at/resolved_by, commits, refreshes."""
    existing_cb = _make_circuit_breaker(id="cb-resolve-1", status="triggered")
    # Make it a mutable object that the service can update in place
    mutable_cb = SimpleNamespace(
        id="cb-resolve-1",
        level=1,
        status="triggered",
        triggered_by="test",
        trigger_reason="reason",
        triggered_at=existing_cb.triggered_at,
        resolved_at=None,
        resolved_by=None,
    )

    session = _make_session()
    session.execute.return_value = _exec_result(scalar_one_or_none=mutable_cb)

    async def fake_refresh(obj):
        pass  # already mutated in-place by service

    session.refresh.side_effect = fake_refresh

    with patch("app.services.circuit_breaker_service.CircuitBreakerResponse.model_validate") as mock_validate:
        mock_validate.side_effect = lambda obj: SimpleNamespace(
            id=obj.id,
            level=obj.level,
            status=obj.status,
            triggered_by=obj.triggered_by,
            trigger_reason=obj.trigger_reason,
            triggered_at=obj.triggered_at,
            resolved_at=obj.resolved_at,
            resolved_by=obj.resolved_by,
        )
        svc = CircuitBreakerService(session)
        result = await svc.resolve("cb-resolve-1", resolved_by="on-call-engineer")

    assert result is not None
    assert result.id == "cb-resolve-1"
    assert result.status == "resolved"
    assert result.resolved_by == "on-call-engineer"
    assert result.resolved_at is not None
    session.commit.assert_called_once()
    session.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_cb_resolve_calls_model_validate():
    """Line 57: model_validate called on the (now-mutated) CB object."""
    mutable_cb = SimpleNamespace(
        id="cb-mv-1", level=2, status="triggered",
        triggered_by="bot", trigger_reason="over budget",
        triggered_at=datetime.now(timezone.utc),
        resolved_at=None, resolved_by=None,
    )

    session = _make_session()
    session.execute.return_value = _exec_result(scalar_one_or_none=mutable_cb)
    session.refresh.side_effect = AsyncMock()

    with patch(
        "app.services.circuit_breaker_service.CircuitBreakerResponse.model_validate"
    ) as mock_validate:
        response_obj = SimpleNamespace(
            id="cb-mv-1", level=2, status="resolved",
            triggered_by="bot", trigger_reason="over budget",
            triggered_at=mutable_cb.triggered_at,
            resolved_at=datetime.now(timezone.utc), resolved_by="admin",
        )
        mock_validate.return_value = response_obj

        svc = CircuitBreakerService(session)
        result = await svc.resolve("cb-mv-1", resolved_by="admin")

    mock_validate.assert_called_once_with(mutable_cb)
    assert result is response_obj
