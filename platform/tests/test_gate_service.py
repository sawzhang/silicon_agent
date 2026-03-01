"""Mock-session unit tests for GateService.

All session.execute() calls are mocked so every line after `await` is covered
within the same trace context — fixing the coverage.py / Python 3.13
sys.monitoring coroutine-resume blind-spot.

Covered uncovered lines:
  gate_service.py: 40-48, 60-62, 68-77, 83-102, 109-119, 136-142, 188-197
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.gate_service import (
    GateService,
    _extract_gate_feedback,
    _llm_extract_gate_lesson,
)
from app.schemas.gate import GateApproveRequest, GateRejectRequest, GateReviseRequest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session() -> AsyncMock:
    session = AsyncMock()
    return session


def _exec_result(*, scalar=None, scalars_list=None):
    result = MagicMock()
    result.scalar.return_value = scalar
    result.scalar_one_or_none.return_value = scalar
    if scalars_list is not None:
        sc = MagicMock()
        sc.all.return_value = scalars_list
        result.scalars.return_value = sc
    return result


def _make_gate(
    *,
    id: str = "g-1",
    gate_type: str = "review",
    task_id: str = "t-1",
    agent_role: str = "review",
    status: str = "pending",
    content: dict | None = None,
    review_comment: str | None = None,
    reviewer: str | None = None,
    reviewed_at: datetime | None = None,
    retry_count: int = 0,
    is_dynamic: bool = False,
    revised_content: str | None = None,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        gate_type=gate_type,
        task_id=task_id,
        agent_role=agent_role,
        status=status,
        content=content,
        review_comment=review_comment,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        retry_count=retry_count,
        is_dynamic=is_dynamic,
        revised_content=revised_content,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _gate_response_from_ns(g: SimpleNamespace):
    """Return a GateDetailResponse built from a SimpleNamespace gate."""
    from app.schemas.gate import GateDetailResponse
    return GateDetailResponse(
        id=g.id,
        gate_type=g.gate_type,
        task_id=g.task_id,
        agent_role=g.agent_role,
        status=g.status,
        content=g.content,
        reviewer=g.reviewer,
        review_comment=g.review_comment,
        reviewed_at=g.reviewed_at,
        created_at=g.created_at,
        retry_count=g.retry_count,
        is_dynamic=g.is_dynamic,
        revised_content=g.revised_content,
    )


# We patch model_validate so all GateDetailResponse construction works on SimpleNamespace
def _patch_model_validate():
    from app.schemas.gate import GateDetailResponse
    return patch.object(
        GateDetailResponse,
        "model_validate",
        side_effect=_gate_response_from_ns,
    )


# ── list_gates ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_gates_no_filters():
    """Lines 40-53: list_gates with no status/task_id returns all gates."""
    now = datetime.now(timezone.utc)
    g1 = _make_gate(id="g-1", status="pending", created_at=now)
    g2 = _make_gate(id="g-2", status="approved", created_at=now)

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=2),            # count query
        _exec_result(scalars_list=[g1, g2]),  # items query
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.list_gates()

    assert result.total == 2
    assert result.page == 1
    assert result.page_size == 20
    assert len(result.items) == 2
    ids = [item.id for item in result.items]
    assert "g-1" in ids and "g-2" in ids


@pytest.mark.asyncio
async def test_list_gates_with_status_filter():
    """Lines 32-34: status filter applied to both count and data queries."""
    now = datetime.now(timezone.utc)
    g1 = _make_gate(id="g-1", status="pending", created_at=now)

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=1),
        _exec_result(scalars_list=[g1]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.list_gates(status="pending")

    assert result.total == 1
    assert result.items[0].status == "pending"


@pytest.mark.asyncio
async def test_list_gates_with_task_id_filter():
    """Lines 35-37: task_id filter applied (line 40-48 body)."""
    now = datetime.now(timezone.utc)
    g1 = _make_gate(id="g-1", task_id="task-abc", status="pending", created_at=now)

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=1),
        _exec_result(scalars_list=[g1]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.list_gates(task_id="task-abc")

    assert result.total == 1
    assert result.items[0].task_id == "task-abc"


@pytest.mark.asyncio
async def test_list_gates_empty():
    """Lines 40-53: list_gates returns empty when no gates found."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=0),
        _exec_result(scalars_list=[]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.list_gates()

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_gates_pagination():
    """Lines 42-43: pagination offset/limit applied."""
    session = _make_session()
    now = datetime.now(timezone.utc)
    g1 = _make_gate(id="g-pg-1", created_at=now)

    session.execute.side_effect = [
        _exec_result(scalar=10),
        _exec_result(scalars_list=[g1]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.list_gates(page=2, page_size=5)

    assert result.page == 2
    assert result.page_size == 5
    assert result.total == 10


# ── get_gate ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_gate_found():
    """Lines 60-62: get_gate returns GateDetailResponse when found."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(id="g-found", status="pending", created_at=now)

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.get_gate("g-found")

    assert result is not None
    assert result.id == "g-found"
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_get_gate_not_found():
    """Lines 60-61: get_gate returns None when gate doesn't exist."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalar=None)

    svc = GateService(session)
    result = await svc.get_gate("nonexistent-gate")

    assert result is None


# ── approve ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_gate_found():
    """Lines 68-77: approve sets status=approved and reviewer/comment."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(id="g-ap-1", status="pending", created_at=now)

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateApproveRequest(reviewer="alice", comment="Looks good")

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.approve("g-ap-1", request)

    assert gate.status == "approved"
    assert gate.reviewer == "alice"
    assert gate.review_comment == "Looks good"
    assert gate.reviewed_at is not None
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(gate)
    assert result is not None
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_approve_gate_not_found():
    """Lines 69-70: approve returns None when gate doesn't exist."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalar=None)

    request = GateApproveRequest(reviewer="dev")
    svc = GateService(session)
    result = await svc.approve("nonexistent", request)

    assert result is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_gate_no_comment():
    """Lines 71-76: approve works when comment is None."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(id="g-ap-2", status="pending", created_at=now)

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateApproveRequest(reviewer="bob")

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.approve("g-ap-2", request)

    assert gate.status == "approved"
    assert gate.review_comment is None
    assert result is not None


# ── reject ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reject_gate_not_found():
    """Lines 83-85: reject returns None when gate doesn't exist."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalar=None)

    request = GateRejectRequest(reviewer="dev", comment="No gate")
    svc = GateService(session)
    result = await svc.reject("nonexistent", request)

    assert result is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_gate_memory_disabled():
    """Lines 86-92: reject sets status=rejected; feedback skipped when MEMORY_ENABLED=False."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(
        id="g-rj-1", status="pending", created_at=now,
        content={"summary": "Stage output"},
    )

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateRejectRequest(reviewer="carol", comment="Needs rework")

    with _patch_model_validate(), \
         patch("app.services.gate_service.settings") as mock_settings:
        mock_settings.MEMORY_ENABLED = False
        mock_settings.SKILL_FEEDBACK_ENABLED = True
        svc = GateService(session)
        result = await svc.reject("g-rj-1", request)

    assert gate.status == "rejected"
    assert gate.reviewer == "carol"
    assert gate.review_comment == "Needs rework"
    assert gate.reviewed_at is not None
    session.commit.assert_awaited_once()
    assert result is not None
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_reject_gate_with_memory_enabled_calls_feedback():
    """Lines 94-100: reject with MEMORY_ENABLED calls _extract_gate_feedback."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(
        id="g-rj-2", task_id="t-with-mem", status="pending", created_at=now,
        content={"summary": "Some output"},
    )

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateRejectRequest(reviewer="dave", comment="Fix it")

    mock_extract = AsyncMock()

    with _patch_model_validate(), \
         patch("app.services.gate_service.settings") as mock_settings, \
         patch("app.services.gate_service._extract_gate_feedback", mock_extract):
        mock_settings.MEMORY_ENABLED = True
        mock_settings.SKILL_FEEDBACK_ENABLED = True
        svc = GateService(session)
        result = await svc.reject("g-rj-2", request)

    assert result is not None
    mock_extract.assert_awaited_once_with(session, gate)


@pytest.mark.asyncio
async def test_reject_gate_feedback_exception_swallowed():
    """Lines 97-100: feedback extraction exceptions are caught, rejection still succeeds."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(
        id="g-rj-3", task_id="t-exc", status="pending", created_at=now,
        content={"summary": "Output"},
    )

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateRejectRequest(reviewer="eve", comment="Failure")

    with _patch_model_validate(), \
         patch("app.services.gate_service.settings") as mock_settings, \
         patch("app.services.gate_service._extract_gate_feedback",
               AsyncMock(side_effect=RuntimeError("DB exploded"))):
        mock_settings.MEMORY_ENABLED = True
        mock_settings.SKILL_FEEDBACK_ENABLED = True
        svc = GateService(session)
        result = await svc.reject("g-rj-3", request)

    # Rejection should succeed despite extraction failure
    assert result is not None
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_reject_gate_skill_feedback_disabled_skips_feedback():
    """Line 94: SKILL_FEEDBACK_ENABLED=False → feedback extraction skipped."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(id="g-rj-4", task_id="t-rj-4", status="pending", created_at=now)

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateRejectRequest(reviewer="frank", comment="Disabled feedback")

    mock_extract = AsyncMock()

    with _patch_model_validate(), \
         patch("app.services.gate_service.settings") as mock_settings, \
         patch("app.services.gate_service._extract_gate_feedback", mock_extract):
        mock_settings.MEMORY_ENABLED = True
        mock_settings.SKILL_FEEDBACK_ENABLED = False  # disabled
        svc = GateService(session)
        result = await svc.reject("g-rj-4", request)

    assert result is not None
    # _extract_gate_feedback should NOT be called when SKILL_FEEDBACK_ENABLED=False
    mock_extract.assert_not_awaited()


# ── revise ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revise_gate_found():
    """Lines 109-119: revise sets status=revised with revised_content."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(id="g-rv-1", status="pending", created_at=now)

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateReviseRequest(
        reviewer="grace", comment="Updated", revised_content="New spec content"
    )

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.revise("g-rv-1", request)

    assert gate.status == "revised"
    assert gate.reviewer == "grace"
    assert gate.review_comment == "Updated"
    assert gate.revised_content == "New spec content"
    assert gate.reviewed_at is not None
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(gate)
    assert result is not None
    assert result.status == "revised"


@pytest.mark.asyncio
async def test_revise_gate_not_found():
    """Lines 109-111: revise returns None for missing gate."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalar=None)

    request = GateReviseRequest(reviewer="dev", comment="No gate")
    svc = GateService(session)
    result = await svc.revise("nonexistent", request)

    assert result is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revise_gate_no_revised_content():
    """Lines 109-119: revise with revised_content=None (optional field)."""
    now = datetime.now(timezone.utc)
    gate = _make_gate(id="g-rv-2", status="pending", created_at=now)

    session = _make_session()
    session.execute.return_value = _exec_result(scalar=gate)

    request = GateReviseRequest(reviewer="henry", comment="Minor tweaks")
    # revised_content defaults to None

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.revise("g-rv-2", request)

    assert gate.status == "revised"
    assert gate.revised_content is None
    assert result is not None


# ── get_history ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_basic():
    """Lines 136-142: get_history returns approved + rejected gates."""
    now = datetime.now(timezone.utc)
    approved = _make_gate(id="g-hist-1", status="approved", reviewed_at=now, created_at=now)
    rejected = _make_gate(id="g-hist-2", status="rejected", reviewed_at=now, created_at=now)

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=2),
        _exec_result(scalars_list=[approved, rejected]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.get_history()

    assert result.total == 2
    assert result.page == 1
    assert result.page_size == 20
    assert len(result.items) == 2
    statuses = {item.status for item in result.items}
    assert statuses == {"approved", "rejected"}


@pytest.mark.asyncio
async def test_get_history_empty():
    """Lines 136-142: get_history returns empty when no resolved gates."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=0),
        _exec_result(scalars_list=[]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.get_history()

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_get_history_pagination():
    """Lines 138-139: history pagination offset/limit applied."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=50),
        _exec_result(scalars_list=[]),
    ]

    with _patch_model_validate():
        svc = GateService(session)
        result = await svc.get_history(page=3, page_size=10)

    assert result.page == 3
    assert result.page_size == 10
    assert result.total == 50


# ── _extract_gate_feedback ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_gate_feedback_no_comment_no_summary():
    """Lines 157-158: returns early when both comment and summary are empty."""
    session = _make_session()
    gate = _make_gate(
        id="g-ef-1",
        task_id="t-1",
        review_comment="",
        content={},  # no 'summary' key
    )
    gate.review_comment = ""

    await _extract_gate_feedback(session, gate)

    # No DB calls should have been made
    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_gate_feedback_no_task_found():
    """Lines 164-166: returns early when task lookup returns None."""
    session = _make_session()
    gate = _make_gate(
        id="g-ef-2",
        task_id="nonexistent-task",
        review_comment="Some feedback",
    )
    # Task lookup returns None
    session.execute.return_value = _exec_result(scalar=None)

    await _extract_gate_feedback(session, gate)

    # Should have done one execute (task lookup) then returned
    session.execute.assert_awaited_once()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_extract_gate_feedback_no_project_id_skips_memory():
    """Lines 187-197: feedback written to DB but memory skipped when task.project_id is None."""
    session = _make_session()

    task = SimpleNamespace(
        id="t-noproj",
        title="No Project Task",
        project_id=None,
    )
    gate = _make_gate(
        id="g-ef-3",
        gate_type="review",
        task_id="t-noproj",
        agent_role="review",
        review_comment="Edge case missing",
        content={"summary": "Stage summary"},
    )

    session.execute.return_value = _exec_result(scalar=task)

    mock_lesson = AsyncMock(return_value="Always handle edge cases")
    mock_store_cls = MagicMock()

    with patch("app.services.gate_service._llm_extract_gate_lesson", mock_lesson), \
         patch("app.worker.memory.ProjectMemoryStore", mock_store_cls):
        await _extract_gate_feedback(session, gate)

    mock_lesson.assert_awaited_once_with("Edge case missing", "Stage summary")
    session.add.assert_called_once()  # SkillFeedbackModel added
    session.commit.assert_awaited_once()
    mock_store_cls.assert_not_called()  # Memory store NOT used


@pytest.mark.asyncio
async def test_extract_gate_feedback_with_project_id_writes_memory():
    """Lines 187-197: with project_id, feedback AND memory both written."""
    session = _make_session()

    task = SimpleNamespace(
        id="t-withproj",
        title="Project Task",
        project_id="proj-abc",
    )
    gate = _make_gate(
        id="g-ef-4",
        gate_type="spec",
        task_id="t-withproj",
        agent_role="spec",
        review_comment="Missing validation",
        content={"summary": "Spec review summary"},
    )

    session.execute.return_value = _exec_result(scalar=task)

    mock_lesson = AsyncMock(return_value="Always validate spec before implementation")

    mock_store_instance = AsyncMock()
    mock_store_instance.add_entries = AsyncMock()
    mock_store_cls = MagicMock(return_value=mock_store_instance)

    mock_entry = SimpleNamespace(content="lesson")
    mock_memory_entry_cls = MagicMock()
    mock_memory_entry_cls.create = MagicMock(return_value=mock_entry)

    with patch("app.services.gate_service._llm_extract_gate_lesson", mock_lesson), \
         patch("app.worker.memory.ProjectMemoryStore", mock_store_cls), \
         patch("app.worker.memory.MemoryEntry", mock_memory_entry_cls):
        await _extract_gate_feedback(session, gate)

    mock_lesson.assert_awaited_once()
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    mock_store_cls.assert_called_once_with("proj-abc")
    mock_store_instance.add_entries.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_gate_feedback_summary_only():
    """Lines 154-158: summary from content dict used when comment is empty."""
    session = _make_session()

    task = SimpleNamespace(id="t-sum", title="Summary Task", project_id=None)
    gate = _make_gate(
        id="g-ef-5",
        gate_type="review",
        task_id="t-sum",
        agent_role="review",
        review_comment="",  # no comment
        content={"summary": "There is a summary"},
    )
    gate.review_comment = ""

    session.execute.return_value = _exec_result(scalar=task)

    mock_lesson = AsyncMock(return_value="Learned from summary")

    with patch("app.services.gate_service._llm_extract_gate_lesson", mock_lesson):
        await _extract_gate_feedback(session, gate)

    # Called with ("", "There is a summary")
    mock_lesson.assert_awaited_once_with("", "There is a summary")


# ── _llm_extract_gate_lesson ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_extract_gate_lesson_success():
    """Lines 207-223: LLM returns content, stripped and returned."""
    mock_response = SimpleNamespace(content="  Always validate inputs  ")
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value=mock_response)

    with patch("app.integration.llm_client.get_llm_client", return_value=mock_client):
        result = await _llm_extract_gate_lesson("missing validation", "stage summary")

    assert result == "Always validate inputs"


@pytest.mark.asyncio
async def test_llm_extract_gate_lesson_empty_response_falls_back():
    """Lines 223-228: LLM returns empty string → falls back to comment."""
    mock_response = SimpleNamespace(content="   ")  # only whitespace
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value=mock_response)

    with patch("app.integration.llm_client.get_llm_client", return_value=mock_client):
        result = await _llm_extract_gate_lesson("raw comment fallback", "summary")

    # lesson.strip() == "" → fallback to comment
    assert result == "raw comment fallback"


@pytest.mark.asyncio
async def test_llm_extract_gate_lesson_exception_falls_back_to_comment():
    """Lines 225-228: any exception → fallback to comment."""
    with patch(
        "app.integration.llm_client.get_llm_client",
        side_effect=RuntimeError("LLM unavailable"),
    ):
        result = await _llm_extract_gate_lesson("raw feedback", "some summary")

    assert result == "raw feedback"


@pytest.mark.asyncio
async def test_llm_extract_gate_lesson_no_comment_falls_back_to_summary():
    """Line 229: comment is empty → fallback returns summary."""
    with patch(
        "app.integration.llm_client.get_llm_client",
        side_effect=RuntimeError("LLM down"),
    ):
        result = await _llm_extract_gate_lesson("", "fallback summary")

    assert result == "fallback summary"


@pytest.mark.asyncio
async def test_llm_extract_gate_lesson_both_empty():
    """Line 229: both comment and summary empty → returns empty string."""
    with patch(
        "app.integration.llm_client.get_llm_client",
        side_effect=RuntimeError("LLM down"),
    ):
        result = await _llm_extract_gate_lesson("", "")

    assert result == ""


@pytest.mark.asyncio
async def test_llm_extract_gate_lesson_chat_exception_falls_back():
    """Lines 225-226: exception from client.chat() → fallback."""
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(side_effect=Exception("timeout"))

    with patch("app.integration.llm_client.get_llm_client", return_value=mock_client):
        result = await _llm_extract_gate_lesson("fallback comment", "summary here")

    assert result == "fallback comment"
